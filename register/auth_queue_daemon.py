#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Container-level durable authorization queue worker.

Registration processes only write atomic job files.  This daemon owns OAuth
minting, survives an individual runner stop, retries transport failures, and
recovers jobs left in ``running`` after a container restart.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import threading
import time
from pathlib import Path
from typing import Any

# A durable job must not be handed to another in-memory pool: keep the running
# file until mint itself reaches a terminal result.
os.environ["CPA_MINT_WORKERS"] = "0"

from auth_export_queue import (  # noqa: E402
    _ensure_durable_dirs,
    _process_job,
    durable_queue_root,
)

_stop = threading.Event()
_claim_lock = threading.Lock()


def _log(message: str) -> None:
    print(f"[auth-daemon] {message}", flush=True)


def _max_attempts() -> int:
    try:
        return max(1, min(int(os.environ.get("AUTH_QUEUE_MAX_ATTEMPTS", "4")), 10))
    except Exception:
        return 4


def _daemon_workers() -> int:
    """Resolve daemon concurrency before the per-run config exists.

    The daemon starts with the container, while ``config.json`` is normally
    materialized only when a registration run starts.  Keep its concurrency
    independent so the startup default cannot accidentally create multiple
    OAuth browsers on the shared sing-box connection.
    """
    try:
        configured = str(os.environ.get("AUTH_QUEUE_WORKERS") or "").strip()
        if configured:
            return max(1, min(int(configured), 4))
    except Exception:
        pass
    return 1


def _registration_active() -> bool:
    """Best-effort guard: rotating sing-box during registration breaks its tab."""
    proc = Path("/proc")
    if not proc.is_dir():
        return False
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmd = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(
                "utf-8", "ignore"
            )
        except Exception:
            continue
        if "runner.py" in cmd and int(entry.name) != os.getpid():
            return True
    return False


def _rotate_bad_singbox_node(reason: str) -> bool:
    if _registration_active():
        _log("注册仍在运行，TLS 故障任务延后；本次不切换共享 sing-box 节点")
        return False
    try:
        from pools import is_singbox_proxy_mode, rotate_singbox_node

        if is_singbox_proxy_mode():
            return bool(rotate_singbox_node(reason))
    except Exception as exc:
        _log(f"sing-box 节点切换失败: {exc}")
    return False


def _recover_running() -> None:
    dirs = _ensure_durable_dirs()
    recovered = 0
    for path in dirs["running"].glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            data["run_at"] = min(float(data.get("run_at") or time.time()), time.time())
            data["recovered_at"] = time.time()
            target = dirs["pending"] / path.name
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(path, target)
            recovered += 1
        except Exception as exc:
            _log(f"恢复运行中任务失败 file={path.name}: {exc}")
    if recovered:
        _log(f"容器重启恢复任务 n={recovered}")


def _read_job(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        _log(f"损坏任务 file={path.name}: {exc}")
        return None


def _claim_due_job() -> tuple[Path, dict[str, Any]] | None:
    dirs = _ensure_durable_dirs()
    now = time.time()
    with _claim_lock:
        for pending in sorted(dirs["pending"].glob("*.json")):
            job = _read_job(pending)
            if job is None:
                try:
                    os.replace(pending, dirs["failed"] / pending.name)
                except OSError:
                    pass
                continue
            if float(job.get("run_at") or 0) > now:
                continue
            running = dirs["running"] / pending.name
            try:
                os.replace(pending, running)
            except OSError:
                continue
            return running, job
    return None


def _safe_result(job: dict[str, Any], result: dict[str, Any]) -> dict[str, Any]:
    email = str(job.get("email") or "").strip().lower()
    return {
        "job_id": str(job.get("job_id") or ""),
        "email_hash": hashlib.sha256(email.encode("utf-8")).hexdigest()[:16] if email else "",
        "ok": bool(result.get("ok")),
        "status": str(result.get("status") or "unknown")[:160],
        "error": str(result.get("error") or "")[:300],
        "attempt": int(job.get("attempt") or 0),
        "finished_at": time.time(),
    }


def _write_terminal(path: Path, job: dict[str, Any], result: dict[str, Any]) -> None:
    dirs = _ensure_durable_dirs()
    target_dir = dirs["done"] if result.get("ok") else dirs["failed"]
    terminal = target_dir / path.name
    terminal.write_text(
        json.dumps(_safe_result(job, result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    try:
        os.chmod(terminal, 0o600)
    except OSError:
        pass
    path.unlink(missing_ok=True)


def _reschedule(path: Path, job: dict[str, Any], result: dict[str, Any]) -> None:
    dirs = _ensure_durable_dirs()
    attempt = int(job.get("attempt") or 0) + 1
    delay = min(300, 45 * (2 ** max(0, attempt - 1)))
    statuses = [str(x) for x in (result.get("statuses") or [])]
    status = str(result.get("status") or "")
    tls_failure = status.startswith("tls_") or any(s.startswith("tls_") for s in statuses)
    if tls_failure:
        _rotate_bad_singbox_node(f"OAuth TLS preflight: {status or 'TLS failure'}")
        if _registration_active():
            delay = max(delay, 120)
    job["attempt"] = attempt
    job["run_at"] = time.time() + delay
    job["last_status"] = status[:160]
    job["last_error"] = str(result.get("error") or "")[:300]
    name = f"{int(job['run_at'] * 1000):013d}_{job.get('job_id') or path.stem}.json"
    tmp = dirs["tmp"] / f".{name}.{os.getpid()}.tmp"
    tmp.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")
    try:
        os.chmod(tmp, 0o600)
    except OSError:
        pass
    os.replace(tmp, dirs["pending"] / name)
    path.unlink(missing_ok=True)
    _log(
        f"任务重试 job={str(job.get('job_id') or '')[:12]} "
        f"attempt={attempt}/{_max_attempts()} delay={delay}s status={status or 'unknown'}"
    )


def _worker() -> None:
    while not _stop.is_set():
        claimed = _claim_due_job()
        if claimed is None:
            _stop.wait(1.0)
            continue
        path, job = claimed
        job_id = str(job.get("job_id") or path.stem)
        _log(
            f"开始 job={job_id[:12]} attempt={int(job.get('attempt') or 0) + 1} "
            f"email_hash={_safe_result(job, {}).get('email_hash')}"
        )
        try:
            result = _process_job(job)
            if not isinstance(result, dict):
                result = {"ok": False, "status": "worker_error", "error": "empty result"}
        except Exception as exc:
            result = {
                "ok": False,
                "status": "worker_error",
                "error": str(exc),
                "retryable": True,
            }
        retryable = bool(result.get("retryable"))
        attempts_used = int(job.get("attempt") or 0) + 1
        if not result.get("ok") and retryable and attempts_used < _max_attempts():
            _reschedule(path, job, result)
            continue
        _write_terminal(path, job, result)
        _log(
            f"结束 job={job_id[:12]} ok={bool(result.get('ok'))} "
            f"status={result.get('status') or 'unknown'}"
        )


def _prune_terminal(limit: int = 500) -> None:
    dirs = _ensure_durable_dirs()
    for name in ("done", "failed"):
        files = sorted(dirs[name].glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[limit:]:
            try:
                old.unlink()
            except OSError:
                pass


def main() -> int:
    os.environ["AUTH_QUEUE_DAEMON"] = "1"
    _recover_running()
    _prune_terminal()
    workers = _daemon_workers()
    _log(f"启动 root={durable_queue_root()} workers={workers} inline_mint=1")

    def _handle_stop(_signum, _frame) -> None:
        _stop.set()

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    threads = [
        threading.Thread(target=_worker, name=f"auth-daemon-w{i + 1}", daemon=True)
        for i in range(workers)
    ]
    for thread in threads:
        thread.start()
    while not _stop.wait(5.0):
        _prune_terminal()
    for thread in threads:
        thread.join(timeout=10.0)
    _log("停止")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
