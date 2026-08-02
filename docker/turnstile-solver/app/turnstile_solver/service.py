from __future__ import annotations

import asyncio
import logging
from typing import Any, Protocol

from .browser_runtime import BrowserRuntime, StateCallback
from .config import SolverConfig
from .models import SolveOutcome, TaskRecord, TaskSpec, TaskState
from .result_store import ResultStore
from .security import validate_task


logger = logging.getLogger(__name__)


class Runner(Protocol):
    async def start(self) -> None: ...
    async def close(self) -> None: ...
    async def solve(self, spec: TaskSpec, on_state: StateCallback) -> SolveOutcome: ...


class SolverService:
    def __init__(self, config: SolverConfig, runner: Runner | None = None) -> None:
        self.config = config
        self.store = ResultStore(config)
        self.runner: Runner = runner or BrowserRuntime(config)
        self.queue: asyncio.Queue[str] = asyncio.Queue(maxsize=config.queue_size)
        self._workers: list[asyncio.Task[None]] = []
        self._started = False
        self._start_lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            await self.runner.start()
            self._workers = [
                asyncio.create_task(self._worker(index), name=f"turnstile-worker-{index}")
                for index in range(self.config.workers)
            ]
            self._started = True

    async def close(self) -> None:
        workers, self._workers = self._workers, []
        for worker in workers:
            worker.cancel()
        if workers:
            await asyncio.gather(*workers, return_exceptions=True)
        await self.runner.close()
        self._started = False

    async def submit(self, spec: TaskSpec) -> TaskRecord:
        validate_task(spec, self.config)
        if not self._started:
            await self.start()
        if self.queue.full():
            raise asyncio.QueueFull
        record = await self.store.create(spec)
        try:
            self.queue.put_nowait(record.task_id)
        except asyncio.QueueFull:
            await self.store.fail(record.task_id, "ERROR_QUEUE_FULL", "solver queue is full")
            raise
        return record

    async def result(self, task_id: str) -> TaskRecord | None:
        return await self.store.get(task_id)

    async def stats(self) -> dict[str, Any]:
        values = await self.store.stats()
        values.update(
            {
                "queueDepth": self.queue.qsize(),
                "queueCapacity": self.config.queue_size,
                "workers": self.config.workers,
                "backend": self.config.backend,
                "started": self._started,
            }
        )
        return values

    async def _worker(self, _index: int) -> None:
        while True:
            task_id = await self.queue.get()
            try:
                record = await self.store.get(task_id)
                if record is None:
                    continue

                async def on_state(state: TaskState) -> None:
                    await self.store.state(task_id, state)

                outcome = await asyncio.wait_for(
                    self.runner.solve(record.spec, on_state),
                    timeout=self.config.task_timeout_seconds,
                )
                if not outcome.token.strip():
                    raise RuntimeError("browser runner returned an empty token")
                await self.store.ready(task_id, outcome)
                logger.info("task ready id=%s token_length=%s", task_id[:12], len(outcome.token))
            except asyncio.TimeoutError:
                await self.store.fail(
                    task_id,
                    "ERROR_TASK_TIMEOUT",
                    f"task exceeded {self.config.task_timeout_seconds}s deadline",
                    state=TaskState.EXPIRED,
                )
            except asyncio.CancelledError:
                await self.store.fail(task_id, "ERROR_CANCELLED", "service stopped", state=TaskState.CANCELLED)
                raise
            except Exception as exc:
                logger.warning("task failed id=%s error=%s", task_id[:12], exc)
                await self.store.fail(task_id, "ERROR_TASK_FAILED", str(exc))
            finally:
                self.queue.task_done()
