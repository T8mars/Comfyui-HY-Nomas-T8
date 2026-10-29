"""Check the pinned installed packages without importing GPU libraries or using the network."""
import importlib.metadata
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    lock = ROOT / "requirements.lock.txt"
    if not lock.is_file():
        return 1
    mismatches = []
    checked = 0
    for raw in lock.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.count("==") != 1:
            print(f"Unsupported lock entry: {line}")
            return 1
        name, expected = line.split("==")
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = "MISSING"
        if actual != expected:
            mismatches.append(f"{name}: {actual}, expected {expected}")
        checked += 1
    for message in mismatches:
        print(message)
    if not checked or mismatches:
        return 1
    print(f"Verified {checked} pinned packages; existing runtime is ready.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
