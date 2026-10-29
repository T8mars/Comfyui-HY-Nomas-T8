"""Multipart and image errors against isolated HTTP test endpoints; no inference."""
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import FormData
from aiohttp.test_utils import TestClient, TestServer
from PIL import Image
from nomas_app import server


class UploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="HyNomas upload test ")
        self.root = Path(self.temp.name)
        self.patch = patch.object(server, "ROOT", self.root)
        self.patch.start()
        (self.root / "nomas_app/static").mkdir(parents=True)
        self.studio = server.Studio()
        self.client = TestClient(TestServer(server.make_app(self.studio), host="127.0.0.1"))
        await self.client.start_server()

    async def asyncTearDown(self):
        await self.client.close()
        self.patch.stop()
        self.temp.cleanup()

    async def image(self, payload):
        data = FormData()
        data.add_field("image", payload, filename="test.png", content_type="image/png")
        return await self.client.post("/api/upload", data=data)

    async def test_nonmultipart_is_bad_request(self):
        response = await self.client.post("/api/upload", json={"image": "not multipart"})
        self.assertEqual(response.status, 400, await response.text())

    async def test_truncated_multipart_is_bad_request(self):
        response = await self.client.post("/api/upload", data=b"--boundary\r\nContent-Disposition: form-data; name=\"image\"; filename=\"a.png\"\r\n\r\nabc",
                                         headers={"Content-Type": "multipart/form-data; boundary=boundary"})
        self.assertEqual(response.status, 400, await response.text())

    async def test_decompression_bomb_is_bad_request(self):
        payload = io.BytesIO()
        Image.new("RGB", (32, 32)).save(payload, "PNG")
        with patch.object(Image, "MAX_IMAGE_PIXELS", 100):
            response = await self.image(payload.getvalue())
        self.assertEqual(response.status, 400, await response.text())
        self.assertFalse(list((self.root / "inputs").glob("*")))

    async def test_valid_rgba_is_saved_as_rgb_with_bound_id(self):
        payload = io.BytesIO()
        Image.new("RGBA", (8, 8), (12, 34, 56, 255)).save(payload, "PNG")
        response = await self.image(payload.getvalue())
        self.assertEqual(response.status, 200, await response.text())
        image_id = (await response.json())["id"]
        self.assertRegex(image_id, r"^[0-9a-f]{32}$")
        with Image.open(self.root / "inputs" / f"{image_id}.png") as image:
            self.assertEqual((image.mode, image.size, image.getpixel((0, 0))), ("RGB", (8, 8), (12, 34, 56)))

    async def test_unidentified_image_is_bad_request(self):
        response = await self.image(b"not an image")
        self.assertEqual(response.status, 400, await response.text())

    async def test_malformed_origin_is_forbidden(self):
        response = await self.client.get("/api/status", headers={"Origin":"http://[broken"})
        self.assertEqual(response.status, 403, await response.text())

    async def test_cross_scheme_origin_is_forbidden(self):
        host = self.client.make_url("/").authority
        response = await self.client.get("/api/status", headers={"Origin":f"https://{host}"})
        self.assertEqual(response.status, 403, await response.text())

    async def test_same_origin_is_allowed(self):
        response = await self.client.get("/api/status", headers={"Origin":str(self.client.make_url("/")).rstrip("/")})
        self.assertEqual(response.status, 200, await response.text())


if __name__ == "__main__":
    unittest.main(verbosity=2)
