from __future__ import annotations

import asyncio
import os
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread

import pytest

from turnstile_solver.browser_runtime import BrowserRuntime
from turnstile_solver.config import SolverConfig
from turnstile_solver.models import TaskSpec


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_TURNSTILE_INTEGRATION") != "1",
    reason="set RUN_TURNSTILE_INTEGRATION=1 to run the official test-key browser fixture",
)


def test_cloudflare_official_always_pass_key():
    fixture_dir = Path(__file__).parent / "fixtures"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(SimpleHTTPRequestHandler, directory=str(fixture_dir)),
    )
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()

    async def scenario():
        config = SolverConfig(
            backend="chromium",
            workers=1,
            allow_private_targets=True,
            automatic_wait_ms=30_000,
            interaction_wait_ms=10_000,
        )
        runtime = BrowserRuntime(config)
        await runtime.start()
        try:
            async def state(_value):
                return None

            return await runtime.solve(
                TaskSpec(
                    f"http://127.0.0.1:{server.server_port}/turnstile_test.html",
                    "1x00000000000000000000AA",
                ),
                state,
            )
        finally:
            await runtime.close()

    try:
        result = asyncio.run(scenario())
        assert result.token
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
