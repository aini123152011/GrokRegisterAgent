from __future__ import annotations

import asyncio
import time

import pytest

from turnstile_solver.api import create_app
from turnstile_solver.config import SolverConfig
from turnstile_solver.models import SolveOutcome, TaskSpec, TaskState
from turnstile_solver.result_store import ResultStore
from turnstile_solver.security import ValidationError, validate_task
from turnstile_solver.service import SolverService


class FakeRunner:
    def __init__(self, token: str = "fixture-token") -> None:
        self.token = token
        self.started = False

    async def start(self) -> None:
        self.started = True

    async def close(self) -> None:
        self.started = False

    async def solve(self, _spec, on_state):
        await on_state(TaskState.NAVIGATE)
        return SolveOutcome(self.token, attempts=1, elapsed_ms=12)


def config(**changes) -> SolverConfig:
    values = {
        "workers": 1,
        "queue_size": 2,
        "allow_private_targets": True,
        "task_timeout_seconds": 2,
    }
    values.update(changes)
    return SolverConfig(**values)


def test_service_completes_task_and_preserves_creation_time():
    async def scenario():
        service = SolverService(config(), FakeRunner())
        record = await service.submit(TaskSpec("http://127.0.0.1/widget", "test-key"))
        created = record.created_at
        await asyncio.wait_for(service.queue.join(), timeout=1)
        result = await service.result(record.task_id)
        assert result is not None
        assert result.state is TaskState.READY
        assert result.token == "fixture-token"
        assert result.created_at == created
        assert result.updated_at >= created
        await service.close()

    asyncio.run(scenario())


def test_queue_has_backpressure():
    class BlockingRunner(FakeRunner):
        def __init__(self):
            super().__init__()
            self.entered = asyncio.Event()
            self.release = asyncio.Event()

        async def solve(self, _spec, on_state):
            await on_state(TaskState.NAVIGATE)
            self.entered.set()
            await self.release.wait()
            return SolveOutcome(self.token)

    async def scenario():
        runner = BlockingRunner()
        service = SolverService(config(queue_size=1), runner)
        await service.submit(TaskSpec("http://127.0.0.1/one", "key"))
        await asyncio.wait_for(runner.entered.wait(), timeout=1)
        await service.submit(TaskSpec("http://127.0.0.1/two", "key"))
        with pytest.raises(asyncio.QueueFull):
            await service.submit(TaskSpec("http://127.0.0.1/three", "key"))
        runner.release.set()
        await asyncio.wait_for(service.queue.join(), timeout=1)
        await service.close()

    asyncio.run(scenario())


def test_result_store_expires_tokens_without_logging_or_mutating_created_at():
    async def scenario():
        cfg = config(token_ttl_seconds=1)
        store = ResultStore(cfg)
        record = await store.create(TaskSpec("http://127.0.0.1/", "key"))
        await store.ready(record.task_id, SolveOutcome("secret-token"))
        stored = await store.get(record.task_id)
        assert stored is not None
        stored.updated_at = time.time() - 2
        assert await store.get(record.task_id) is None

    asyncio.run(scenario())


def test_private_targets_are_rejected_by_default():
    with pytest.raises(ValidationError, match="private"):
        validate_task(TaskSpec("http://127.0.0.1/widget", "key"), SolverConfig())


def test_api_is_yescaptcha_compatible_and_authenticates_all_task_routes():
    async def scenario():
        cfg = config(api_key="secret")
        service = SolverService(cfg, FakeRunner("api-token"))
        app = create_app(cfg, service)
        async with app.test_app():
            client = app.test_client()
            denied = await client.post(
                "/createTask",
                json={"task": {"websiteURL": "http://127.0.0.1/", "websiteKey": "key"}},
            )
            assert denied.status_code == 401

            created = await client.post(
                "/createTask",
                headers={"X-API-Key": "secret"},
                json={
                    "clientKey": "secret",
                    "task": {"websiteURL": "http://127.0.0.1/", "websiteKey": "key"},
                },
            )
            body = await created.get_json()
            assert body["errorId"] == 0
            await asyncio.wait_for(service.queue.join(), timeout=1)
            result = await client.post(
                "/getTaskResult",
                headers={"X-API-Key": "secret"},
                json={"clientKey": "secret", "taskId": body["taskId"]},
            )
            result_body = await result.get_json()
            assert result_body["status"] == "ready"
            assert result_body["solution"]["token"] == "api-token"

    asyncio.run(scenario())
