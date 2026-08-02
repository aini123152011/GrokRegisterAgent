from __future__ import annotations

import asyncio
import hmac
from typing import Any

from quart import Quart, jsonify, request

from .config import SolverConfig
from .models import TaskSpec
from .security import ValidationError
from .service import SolverService


def _error(code: str, description: str, status: int) -> tuple[Any, int]:
    return jsonify({"errorId": 1, "errorCode": code, "errorDescription": description}), status


def create_app(config: SolverConfig | None = None, service: SolverService | None = None) -> Quart:
    cfg = config or SolverConfig.from_env()
    solver = service or SolverService(cfg)
    app = Quart(__name__)
    app.config["SOLVER_CONFIG"] = cfg
    app.extensions["solver_service"] = solver

    @app.before_serving
    async def start_service() -> None:
        await solver.start()

    @app.after_serving
    async def stop_service() -> None:
        await solver.close()

    @app.before_request
    async def authenticate() -> Any:
        if request.path in {"/", "/health"} or not cfg.api_key:
            return None
        supplied = await _request_key()
        if not supplied or not hmac.compare_digest(supplied, cfg.api_key):
            return _error("ERROR_AUTH", "invalid solver API key", 401)
        return None

    @app.get("/")
    async def index() -> Any:
        return jsonify(
            {
                "service": "GrokRegisterAgent Turnstile Solver",
                "version": 2,
                "endpoints": ["POST /createTask", "POST /getTaskResult", "GET /health"],
            }
        )

    @app.get("/health")
    async def health() -> Any:
        stats = await solver.stats()
        return jsonify({"ok": True, "status": "ready" if stats["started"] else "starting", **stats})

    @app.post("/createTask")
    async def create_task() -> Any:
        data = await request.get_json(silent=True)
        if not isinstance(data, dict):
            return _error("ERROR_BAD_REQUEST", "JSON object required", 400)
        try:
            record = await solver.submit(TaskSpec.from_api(data))
        except ValidationError as exc:
            return _error("ERROR_BAD_REQUEST", str(exc), 400)
        except asyncio.QueueFull:
            return _error("ERROR_QUEUE_FULL", "solver queue is full", 429)
        return jsonify({"errorId": 0, "taskId": record.task_id, "status": "processing"})

    @app.post("/getTaskResult")
    async def get_task_result() -> Any:
        data = await request.get_json(silent=True)
        task_id = str((data or {}).get("taskId") or "").strip() if isinstance(data, dict) else ""
        return await _result_response(solver, task_id)

    @app.get("/turnstile")
    async def legacy_create() -> Any:
        data = {
            "url": request.args.get("url", ""),
            "sitekey": request.args.get("sitekey", ""),
            "action": request.args.get("action", ""),
            "cdata": request.args.get("cdata", ""),
            "proxy": request.args.get("proxy", ""),
        }
        try:
            record = await solver.submit(TaskSpec.from_api(data))
        except ValidationError as exc:
            return _error("ERROR_BAD_REQUEST", str(exc), 400)
        except asyncio.QueueFull:
            return _error("ERROR_QUEUE_FULL", "solver queue is full", 429)
        return jsonify({"errorId": 0, "taskId": record.task_id, "status": "processing"})

    @app.get("/result")
    async def legacy_result() -> Any:
        return await _result_response(solver, str(request.args.get("id") or "").strip())

    @app.post("/reclaim")
    async def reclaim() -> Any:
        if not await _admin_allowed(cfg):
            return _error("ERROR_ADMIN_AUTH", "invalid admin key", 403)
        removed = await solver.store.purge()
        return jsonify({"ok": True, "removed": removed})

    @app.post("/resize")
    async def resize() -> Any:
        if not await _admin_allowed(cfg):
            return _error("ERROR_ADMIN_AUTH", "invalid admin key", 403)
        return _error("ERROR_RESTART_REQUIRED", "set THREAD and restart to resize the browser pool", 409)

    return app


async def _result_response(service: SolverService, task_id: str) -> Any:
    if not task_id:
        return _error("ERROR_BAD_REQUEST", "taskId is required", 400)
    record = await service.result(task_id)
    if record is None:
        return _error("ERROR_TASK_NOT_FOUND", "task does not exist or has expired", 404)
    return jsonify(record.as_api())


async def _request_key() -> str:
    header = str(request.headers.get("X-API-Key") or "").strip()
    authorization = str(request.headers.get("Authorization") or "").strip()
    if authorization.lower().startswith("bearer "):
        header = authorization[7:].strip()
    if header:
        return header
    if request.method == "POST":
        data = await request.get_json(silent=True)
        if isinstance(data, dict):
            return str(data.get("clientKey") or "").strip()
    return str(request.args.get("clientKey") or "").strip()


async def _admin_allowed(config: SolverConfig) -> bool:
    expected = config.admin_key or config.api_key
    if not expected:
        return True
    supplied = str(request.headers.get("X-Admin-Key") or "").strip() or await _request_key()
    return bool(supplied and hmac.compare_digest(supplied, expected))
