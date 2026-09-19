"""Regression test for Pixel inference stream revocation and clean exception propagation."""

import asyncio
import importlib.util
from pathlib import Path
import tempfile
import unittest

import sys
try:
    import httpx
    from fastapi.testclient import TestClient
except ImportError:
    from unittest import SkipTest
    raise SkipTest("httpx and fastapi required")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "bin"))
spec = importlib.util.spec_from_file_location(
    "inference_sharing_app",
    ROOT / "extensions/services/pixel-inference/app/main.py",
)
gateway = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gateway)

from pixel_provider.sharing import SharingStore


class TestPixelStreamRevocation(unittest.TestCase):
    def test_revocation_terminates_stream_without_exception_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            state_dir = Path(tmp)
            state_dir.chmod(0o700)
            store = SharingStore(state_dir)
            issued = store.issue(
                dict(
                    label="Laptop",
                    catalogId="glm",
                    runtimeModelId="GLM",
                    ttlSeconds=3600,
                    maxConcurrent=1,
                    maxOutputTokens=64,
                    deadlineSeconds=3,
                    requestsPerMinute=60,
                ),
                expected_revision=0,
            )
            store.set_enabled(True, expected_revision=1)
            token = issued["credential"]["key"]

            closed = []

            class Revoking(httpx.AsyncByteStream):
                async def __aiter__(self):
                    yield b'data: {"model":"ods/shared"}\n\n'
                    doc = store.load()
                    store.revoke(doc["devices"][0]["id"], expected_revision=doc["revision"])
                    await asyncio.sleep(5)

                async def aclose(self):
                    closed.append(True)

            def backend(request):
                if request.method == "GET":
                    return httpx.Response(200, json={"ods": dict(catalogId="glm", routedModel="GLM", routeSeq=4)})
                return httpx.Response(200, stream=Revoking(), headers={"content-type": "text/event-stream"})

            upstream = httpx.AsyncClient(transport=httpx.MockTransport(backend))
            app = gateway.create_app(store, "http://127.0.0.1:9099", upstream)
            with TestClient(app) as client:
                with self.assertRaises(gateway.ShareError) as ctx:
                    client.post(
                        "/v1/chat/completions",
                        json=dict(model="ods/shared", messages=[{"role": "user", "content": "hello"}], stream=True),
                        headers={"Authorization": "Bearer " + token},
                    )
                self.assertIn("credential_no_longer_valid", str(ctx.exception))
            self.assertEqual(closed, [True])


if __name__ == "__main__":
    unittest.main()
