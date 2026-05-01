import time

import pytest
from aiohttp.test_utils import TestClient, TestServer


@pytest.mark.asyncio
async def test_health_endpoint_returns_status_ok_and_version():
    from src.health import create_health_app

    async with TestClient(TestServer(create_health_app())) as client:
        resp = await client.get("/health")
        assert resp.status == 200
        body = await resp.json()
        assert body == {"status": "ok", "version": "1.0.0"}


@pytest.mark.asyncio
async def test_health_endpoint_responds_within_200ms():
    from src.health import create_health_app

    async with TestClient(TestServer(create_health_app())) as client:
        # warm up — first request includes connection setup overhead
        await client.get("/health")

        start = time.perf_counter()
        resp = await client.get("/health")
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert resp.status == 200
        assert elapsed_ms < 200, f"/health took {elapsed_ms:.1f}ms (>200ms budget)"


@pytest.mark.asyncio
async def test_health_endpoint_returns_json_content_type():
    from src.health import create_health_app

    async with TestClient(TestServer(create_health_app())) as client:
        resp = await client.get("/health")
        assert "application/json" in resp.headers.get("content-type", "")
