"""Deterministic request-body interleavings; no model, GPU or listening server."""
import asyncio
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aiohttp import web
from nomas_app.server import Studio, make_app


class AdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.studio = Studio()
        self.pending = set()
        self.finish = asyncio.Event()
        async def perform(action, payload):
            self.pending.add(asyncio.current_task())
            await self.finish.wait()
        self.studio.perform = perform
        self.studio.locations.save = Mock(return_value=({}, []))
        self.app = make_app(self.studio)
        self.action = next(r.handler for r in self.app.router.routes() if r.method == 'POST' and r.handler.__name__ == 'action')
        self.paths = next(r.handler for r in self.app.router.routes() if r.method == 'POST' and r.handler.__name__ == 'model_paths')

    async def asyncTearDown(self):
        self.finish.set()
        await asyncio.sleep(0)
        await asyncio.gather(*self.pending, return_exceptions=True)
        self.studio.pool.shutdown(wait=True)

    def request(self, payload=None, gate=None, entered=None):
        async def body():
            if entered:
                entered.set()
            if gate:
                await gate.wait()
            if isinstance(payload, Exception):
                raise payload
            return payload or {}
        return types.SimpleNamespace(method='POST', can_read_body=True, match_info={'name':'generate'}, json=body)

    async def test_delayed_generate_cannot_admit_a_second_job(self):
        entered, gate = asyncio.Event(), asyncio.Event()
        delayed = asyncio.create_task(self.action(self.request({}, gate, entered)))
        await entered.wait()
        first = await self.action(self.request({}))
        self.assertEqual(first.status, 202)
        gate.set()
        with self.assertRaises(web.HTTPConflict):
            await delayed

    async def test_delayed_settings_cannot_change_paths_during_generation(self):
        entered, gate = asyncio.Event(), asyncio.Event()
        delayed = asyncio.create_task(self.paths(self.request({}, gate, entered)))
        await entered.wait()
        self.assertEqual((await self.action(self.request({}))).status, 202)
        gate.set()
        with self.assertRaises(web.HTTPConflict):
            await delayed
        self.studio.locations.save.assert_not_called()

    async def test_malformed_json_is_a_bad_request(self):
        for handler in [self.action, self.paths]:
            with self.subTest(handler=handler.__name__):
                with self.assertRaises(web.HTTPBadRequest):
                    await handler(self.request(json.JSONDecodeError('bad JSON', '{', 0)))

    async def test_image_identifier_requires_a_string(self):
        for value in [123, True, [1], {'a':1}]:
            with self.subTest(value=value):
                with self.assertRaises(web.HTTPBadRequest):
                    await self.action(self.request({'image_id':value}))


if __name__ == '__main__':
    unittest.main()
