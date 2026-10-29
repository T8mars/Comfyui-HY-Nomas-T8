"""Third review: new support-layer boundaries, with no inference or external HTTP."""
import asyncio
import hashlib
import importlib.util
import json
import sys
import tempfile
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import patch

STUDIO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(STUDIO))
from aiohttp import web
from nomas_app import server, prompts
from nomas_app.models import ModelLocations
from scripts import range_download as ranges

spec = importlib.util.spec_from_file_location("support_prompt_oracle", STUDIO.parent / "hy_nomas/prompt.py")
oracle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oracle)


class SupportServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="HyNomas support test ")
        self.root = Path(self.temp.name).resolve()
        (self.root / "nomas_app/static").mkdir(parents=True)
        self.studio = server.Studio()
        self.root_patch = patch.object(server, "ROOT", self.root)
        self.root_patch.start()

    async def asyncTearDown(self):
        self.studio.pool.shutdown(wait=True)
        self.root_patch.stop()
        self.temp.cleanup()

    async def test_completed_job_survives_receipt_write_failure(self):
        (self.root / "evidence").write_text("not a directory", encoding="utf-8")
        self.studio.engine = types.SimpleNamespace(export=lambda: {"file":"session/world.mp4"}, cancel_event=threading.Event())
        self.studio.job = {"id":"test", "state":"running"}
        await self.studio.perform("export", {})
        self.assertEqual(self.studio.job["state"], "done")
        self.assertEqual(self.studio.job["result"]["file"], "session/world.mp4")
        self.assertIn("evidence_warning", self.studio.job)

    async def test_invalid_output_path_is_not_found(self):
        app = server.make_app(self.studio)
        output = next(route.handler for route in app.router.routes() if route.method == "GET" and route.handler.__name__ == "output")
        for name in ["bad\0.mp4", "../outside.mp4"]:
            with self.subTest(path=name):
                with self.assertRaises(web.HTTPNotFound):
                    await output(types.SimpleNamespace(match_info={"path":name}))

    async def test_inaccessible_model_probe_reports_missing_in_status(self):
        (self.root / "models.manifest.json").write_text(json.dumps({"models":[dict(id="dit",local="models/m.safetensors",remote="DiT/m.safetensors",size=100)]}),encoding="utf-8")
        locations = ModelLocations(self.root)
        locations.config_file.parent.mkdir()
        locations.config_file.write_text(json.dumps({"model_root":str(self.root)}),encoding="utf-8")
        self.studio.locations = locations
        original = Path.is_file
        def inaccessible(path):
            if path == self.root / "m.safetensors":
                raise PermissionError("isolated model probe denied")
            return original(path)
        with patch.object(Path,"is_file",inaccessible):
            rows = self.studio.models()
        self.assertFalse(rows[0]["ready"])
        self.assertIn("denied",rows[0]["error"])

    async def test_output_traversal_and_valid_video_response(self):
        folder = self.root / "outputs"
        folder.mkdir()
        (folder / "safe.mp4").write_bytes(b"video fixture")
        (self.root / "outside.mp4").write_bytes(b"outside sentinel")
        app=server.make_app(self.studio)
        output=next(route.handler for route in app.router.routes() if route.method=="GET" and route.handler.__name__=="output")
        with self.assertRaises(web.HTTPNotFound):
            await output(types.SimpleNamespace(match_info={"path":"../outside.mp4"}))
        response=await output(types.SimpleNamespace(match_info={"path":"safe.mp4"}))
        self.assertIsInstance(response,web.FileResponse)
        self.assertEqual(response._path,folder / "safe.mp4")


class PromptClockTests(unittest.TestCase):
    def test_fractional_latent_duration_boundaries_use_real_oracle(self):
        for duration, frames in [(1/3,8),(2/3,16),(1/6,4)]:
            with self.subTest(duration=duration):
                document=prompts.add_turn({"seconds":duration})
                self.assertEqual(oracle.pixel_boundaries(document),[0,frames])

    def test_repeated_fractional_turns_do_not_drift(self):
        for duration, frames in [(1/3,8),(2/3,16)]:
            with self.subTest(duration=duration):
                document=None
                for index in range(105):
                    document=prompts.add_turn({"seconds":duration},document)
                pixel=oracle.pixel_boundaries(document)
                self.assertEqual(pixel,[frames*i for i in range(106)])
                self.assertEqual(oracle.latent_boundaries(pixel),[frames//4*i for i in range(106)])

    def test_integer_duration_and_image_still_prefix_remain_exact(self):
        for seconds in [1,2,3,5,6,10]:
            document=prompts.add_turn({"seconds":seconds})
            self.assertEqual(document["segments"][0]["end_time"],f"00:{seconds:02d}.000")
            self.assertEqual(oracle.pixel_boundaries(document),[0,seconds*24])
        image=prompts.image_start_document({"seconds":2})
        self.assertEqual(oracle.pixel_boundaries(image),[0,12,60])
        self.assertEqual(image["segments"][0]["contained_actions"],[])


class RangeRecoveryTests(unittest.TestCase):
    DATA=b"abcdefghijkl"
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix="HyNomas range review ")
        self.root=Path(self.temp.name)
        self.target=self.root/"fixture.bin"
        self.expected=hashlib.sha256(self.DATA).hexdigest()
        self.calls=[]
        self.corrupt=False
        self.bad_range=False

    def tearDown(self): self.temp.cleanup()

    def response(self,request,timeout):
        first,last=map(int,request.get_header("Range").removeprefix("bytes=").split("-"))
        self.calls.append((first,last))
        data=self.DATA[first:last+1]
        if self.corrupt: data=b"?"*len(data)
        content=f"bytes {first}-{last}/{len(self.DATA)}" if not self.bad_range else "bytes 0-999/1000"
        class Response:
            status=206
            headers={"Content-Range":content}
            def read(self,size):return data[:size]
            def __enter__(self):return self
            def __exit__(self,*args):pass
        return Response()

    def download(self):
        def fast_event():
            event=threading.Event()
            event.wait=lambda timeout=None: event.is_set()
            return event
        fake_threading=types.SimpleNamespace(Lock=threading.Lock,Event=fast_event)
        with patch.object(ranges.urllib.request,"urlopen",self.response), patch.object(ranges,"threading",fake_threading):
            return ranges.download("https://fixture.invalid/ranges",self.target,len(self.DATA),self.expected,workers=1,chunk_size=4)

    def test_checksum_failure_can_recover_on_next_real_response(self):
        self.corrupt=True
        with self.assertRaises(ValueError):self.download()
        partial=self.target.with_name(self.target.name+".partial")
        self.assertEqual(partial.read_bytes(),b"?"*len(self.DATA))
        self.corrupt=False
        self.calls.clear()
        self.download()
        self.assertEqual(self.target.read_bytes(),self.DATA)
        self.assertEqual(self.calls,[(0,3),(4,7),(8,11)])

    def test_bool_completed_index_rejected_before_network_or_mutation(self):
        partial=self.target.with_name(self.target.name+".partial")
        ledger=self.target.with_name(self.target.name+".ranges.json")
        partial.write_bytes(self.DATA)
        ledger.write_text(json.dumps({"identity":{"url":"https://fixture.invalid/ranges","size":len(self.DATA),"sha256":self.expected,"chunk_size":4},"done":[True]}),encoding="utf-8")
        before=ledger.read_bytes()
        with self.assertRaises(ValueError):self.download()
        self.assertEqual(self.calls,[])
        self.assertEqual(partial.read_bytes(),self.DATA)
        self.assertEqual(ledger.read_bytes(),before)

    def test_wrong_content_range_never_commits_data(self):
        self.bad_range=True
        with self.assertRaises(RuntimeError):self.download()
        self.assertFalse(self.target.exists())
        self.assertEqual(self.target.with_name(self.target.name+".partial").read_bytes(),b"\0"*len(self.DATA))
        self.assertEqual(json.loads(self.target.with_name(self.target.name+".ranges.json").read_text())["done"],[])

    def test_target_created_during_transfer_is_preserved(self):
        response=self.response
        def create_target(request,timeout):
            self.target.write_bytes(b"user sentinel")
            return response(request,timeout)
        self.response=create_target
        with self.assertRaises(ValueError):self.download()
        self.assertEqual(self.target.read_bytes(),b"user sentinel")
        self.assertEqual(self.target.with_name(self.target.name+".partial").read_bytes(),self.DATA)


if __name__=="__main__": unittest.main(verbosity=2)
