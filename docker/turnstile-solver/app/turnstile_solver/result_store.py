from __future__ import annotations

import asyncio
import time

from .config import SolverConfig
from .models import SolveOutcome, TaskRecord, TaskSpec, TaskState


class ResultStore:
    def __init__(self, config: SolverConfig) -> None:
        self.config = config
        self._items: dict[str, TaskRecord] = {}
        self._lock = asyncio.Lock()

    async def create(self, spec: TaskSpec) -> TaskRecord:
        async with self._lock:
            self._cleanup_locked()
            record = TaskRecord(spec=spec)
            self._items[record.task_id] = record
            self._trim_locked()
            return record

    async def get(self, task_id: str) -> TaskRecord | None:
        async with self._lock:
            self._cleanup_locked()
            return self._items.get(task_id)

    async def state(self, task_id: str, state: TaskState) -> None:
        async with self._lock:
            record = self._items.get(task_id)
            if record and not record.state.terminal:
                record.touch(state)

    async def ready(self, task_id: str, outcome: SolveOutcome) -> None:
        async with self._lock:
            record = self._items.get(task_id)
            if record:
                record.token = outcome.token
                record.attempts = outcome.attempts
                record.elapsed_ms = outcome.elapsed_ms
                record.touch(TaskState.READY)

    async def fail(self, task_id: str, code: str, message: str, *, state: TaskState = TaskState.FAILED) -> None:
        async with self._lock:
            record = self._items.get(task_id)
            if record:
                record.token = ""
                record.error_code = code
                record.error_message = message[:500]
                record.touch(state)

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            self._cleanup_locked()
            values = list(self._items.values())
            return {
                "total": len(values),
                "queued": sum(item.state is TaskState.QUEUED for item in values),
                "processing": sum(not item.state.terminal and item.state is not TaskState.QUEUED for item in values),
                "ready": sum(item.state is TaskState.READY for item in values),
                "failed": sum(item.state in {TaskState.FAILED, TaskState.EXPIRED, TaskState.CANCELLED} for item in values),
            }

    async def purge(self) -> int:
        async with self._lock:
            before = len(self._items)
            self._cleanup_locked()
            return before - len(self._items)

    def _cleanup_locked(self) -> None:
        now = time.time()
        expired: list[str] = []
        for task_id, item in self._items.items():
            ttl = self.config.token_ttl_seconds if item.state is TaskState.READY else self.config.result_ttl_seconds
            if item.state.terminal and now - item.updated_at > ttl:
                expired.append(task_id)
        for task_id in expired:
            self._items.pop(task_id, None)

    def _trim_locked(self) -> None:
        excess = len(self._items) - self.config.max_results
        if excess <= 0:
            return
        ordered = sorted(
            self._items.values(),
            key=lambda item: (not item.state.terminal, item.updated_at),
        )
        for record in ordered[:excess]:
            self._items.pop(record.task_id, None)
