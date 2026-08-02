from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskState(str, Enum):
    QUEUED = "queued"
    ACQUIRE_BROWSER = "acquire_browser"
    CREATE_CONTEXT = "create_context"
    NAVIGATE = "navigate"
    RENDER_WIDGET = "render_widget"
    WAIT_AUTOMATIC = "wait_automatic"
    INTERACT = "interact"
    READY = "ready"
    FAILED = "failed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

    @property
    def terminal(self) -> bool:
        return self in {self.READY, self.FAILED, self.EXPIRED, self.CANCELLED}


@dataclass(frozen=True, slots=True)
class TaskSpec:
    url: str
    sitekey: str
    action: str = ""
    cdata: str = ""
    proxy: str = ""
    backend: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "TaskSpec":
        task = data.get("task") if isinstance(data.get("task"), dict) else data
        url = str(task.get("websiteURL") or task.get("url") or "").strip()
        sitekey = str(task.get("websiteKey") or task.get("sitekey") or "").strip()
        action = str(task.get("action") or "").strip()
        cdata = str(task.get("cData") or task.get("cdata") or "").strip()
        proxy = str(
            task.get("proxy")
            or task.get("proxyURL")
            or task.get("proxyUrl")
            or task.get("proxy_url")
            or ""
        ).strip()
        if not proxy:
            address = str(task.get("proxyAddress") or "").strip()
            port = str(task.get("proxyPort") or "").strip()
            if address and port:
                scheme = str(task.get("proxyType") or "http").strip().lower()
                user = str(task.get("proxyLogin") or task.get("proxyUsername") or "").strip()
                password = str(task.get("proxyPassword") or "")
                auth = f"{user}:{password}@" if user else ""
                proxy = f"{scheme}://{auth}{address}:{port}"
        return cls(
            url=url,
            sitekey=sitekey,
            action=action,
            cdata=cdata,
            proxy=proxy,
            backend=str(task.get("backend") or "").strip().lower(),
        )


@dataclass(frozen=True, slots=True)
class SolveOutcome:
    token: str
    attempts: int = 0
    elapsed_ms: int = 0


@dataclass(slots=True)
class TaskRecord:
    spec: TaskSpec
    task_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    state: TaskState = TaskState.QUEUED
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    token: str = ""
    error_code: str = ""
    error_message: str = ""
    attempts: int = 0
    elapsed_ms: int = 0

    def touch(self, state: TaskState) -> None:
        self.state = state
        self.updated_at = time.time()

    def as_api(self) -> dict[str, Any]:
        if self.state is TaskState.READY and self.token:
            return {
                "errorId": 0,
                "status": "ready",
                "taskId": self.task_id,
                "solution": {"token": self.token},
                "attempts": self.attempts,
                "elapsedMs": self.elapsed_ms,
            }
        if self.state.terminal:
            return {
                "errorId": 1,
                "errorCode": self.error_code or "ERROR_TASK_FAILED",
                "errorDescription": self.error_message or self.state.value,
                "status": "failed",
                "taskId": self.task_id,
            }
        return {
            "errorId": 0,
            "status": "processing",
            "state": self.state.value,
            "taskId": self.task_id,
        }
