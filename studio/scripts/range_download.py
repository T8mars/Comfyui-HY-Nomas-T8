"""Verified HTTP range transfer with bounded retries and a durable completion ledger."""
import argparse
import concurrent.futures
import contextlib
import hashlib
import json
import math
import threading
import time
import urllib.request
from pathlib import Path


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as source:
        for data in iter(lambda: source.read(16 * 1024**2), b""):
            h.update(data)
    return h.hexdigest()


@contextlib.contextmanager
def download_lock(target):
    """An OS-held lock survives crashes as an unlocked file, without stale PID guesses."""
    path = Path(target).with_name(Path(target).name + ".lock")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        if path.stat().st_size == 0:
            handle.write(b"\0")
            handle.flush()
        handle.seek(0)
        import os
        if os.name == "nt":
            import msvcrt
            acquire = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(handle, fcntl.LOCK_UN)
        try:
            acquire()
        except OSError as exc:
            raise RuntimeError(f"Another downloader is using {target}; wait for it to finish.") from exc
        try:
            yield
        finally:
            handle.seek(0)
            release()


def download(url, target, expected_size, expected_sha, workers=8, chunk_size=8 * 1024**2):
    with download_lock(target):
        return _download(url, target, expected_size, expected_sha, workers, chunk_size)


def _download(url, target, expected_size, expected_sha, workers=8, chunk_size=8 * 1024**2):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.stat().st_size == expected_size and sha256(target) == expected_sha:
            print(f"Verified existing {target.name}", flush=True)
            return target
        raise ValueError(f"Existing file differs; preserved without overwrite: {target}")
    partial = target.with_name(target.name + ".partial")
    ledger = target.with_name(target.name + ".ranges.json")
    identity = {"url": url, "size": expected_size, "sha256": expected_sha, "chunk_size": chunk_size}
    done = set()
    if ledger.exists():
        state = json.loads(ledger.read_text(encoding="utf-8"))
        if state.get("identity") != identity:
            raise ValueError(f"Download identity differs; preserve and inspect {ledger}")
        if not partial.exists() or partial.stat().st_size != expected_size:
            raise ValueError(f"Partial file and ledger disagree: {partial}")
        done = set(state["done"])
    else:
        # A previous run may have stopped before the first completed range.
        with partial.open("wb") as output:
            output.truncate(expected_size)
    count = math.ceil(expected_size / chunk_size)
    if any(not isinstance(index, int) or not 0 <= index < count for index in done):
        raise ValueError("Invalid range ledger")
    guard, stop = threading.Lock(), threading.Event()
    started, last_report = time.monotonic(), [0]
    initial = len(done)

    def save():
        temporary = ledger.with_suffix(".tmp")
        temporary.write_text(json.dumps({"identity": identity, "done": sorted(done)}), encoding="utf-8")
        temporary.replace(ledger)

    save()
    print(f"{target.name}: {len(done)}/{count} ranges complete, {workers} connections", flush=True)

    def transfer(index):
        first = index * chunk_size
        last = min(first + chunk_size, expected_size) - 1
        request = urllib.request.Request(url, headers={"Range": f"bytes={first}-{last}",
                                                       "User-Agent": "HyNomas-T8star/1.0"})
        error = None
        for attempt in range(6):
            if stop.is_set():
                return
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    if response.status != 206 or response.headers.get("Content-Range") != f"bytes {first}-{last}/{expected_size}":
                        raise ValueError("Server did not honor the requested byte range")
                    data = response.read(last - first + 2)
                    if len(data) != last - first + 1:
                        raise ValueError("Incomplete byte range")
                with partial.open("r+b", buffering=0) as output:
                    output.seek(first)
                    output.write(data)
                with guard:
                    done.add(index)
                    save()
                    now = time.monotonic()
                    if now - last_report[0] > 15 or len(done) == count:
                        speed = (len(done) - initial) * chunk_size / max(now - started, 1) / 1024**2
                        print(f"{target.name}: {len(done)}/{count} ({len(done)/count:.1%}), {speed:.2f} MiB/s", flush=True)
                        last_report[0] = now
                return
            except Exception as exc:
                error = exc
                if attempt < 5:
                    stop.wait(min(2**attempt, 15))
        stop.set()
        raise RuntimeError(f"Range {index} failed after retries: {type(error).__name__}: {error}")

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(transfer, i) for i in range(count) if i not in done]
        try:
            for future in concurrent.futures.as_completed(futures):
                future.result()
        except BaseException:
            stop.set()
            for future in futures:
                future.cancel()
            raise
    print(f"Hashing {target.name}…", flush=True)
    if sha256(partial) != expected_sha:
        raise ValueError(f"SHA256 mismatch; incomplete file preserved: {partial}")
    if target.exists():
        raise ValueError(f"Target was created during transfer; both files preserved: {target}")
    partial.replace(target)
    ledger.unlink(missing_ok=True)
    print(f"Verified {target.name}: {expected_sha}", flush=True)
    return target


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("target")
    parser.add_argument("--size", type=int, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    download(args.url, args.target, args.size, args.sha256, max(1, min(args.workers, 16)))


if __name__ == "__main__":
    main()
