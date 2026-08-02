from __future__ import annotations

import importlib
import json
import sys
import time
from pathlib import Path

REGISTER_DIR = Path(__file__).resolve().parents[1]
if str(REGISTER_DIR) not in sys.path:
    sys.path.insert(0, str(REGISTER_DIR))


def _reload_queue(monkeypatch, tmp_path):
    monkeypatch.setenv("AUTH_QUEUE_DAEMON", "1")
    monkeypatch.setenv("AUTH_QUEUE_DIR", str(tmp_path / "auth-queue"))
    import auth_export_queue

    return importlib.reload(auth_export_queue)


def test_enqueue_is_atomic_and_durable(monkeypatch, tmp_path):
    queue_mod = _reload_queue(monkeypatch, tmp_path)
    monkeypatch.setattr(
        queue_mod,
        "load_push_flags",
        lambda: {"sso_g2": False, "auth_cpa": False, "auto_auth": True},
    )
    result = queue_mod.enqueue_sso_to_auth(
        sso="sso-secret-value",
        email="person@example.test",
        password="password-secret",
        delay_min_sec=0,
        delay_max_sec=0,
    )

    assert result["queued"] is True
    assert result["durable"] is True
    pending = list((tmp_path / "auth-queue" / "pending").glob("*.json"))
    assert len(pending) == 1
    assert not list((tmp_path / "auth-queue" / "tmp").glob("*"))
    payload = json.loads(pending[0].read_text(encoding="utf-8"))
    assert payload["job_id"] == result["job_id"]
    assert payload["sso"] == "sso-secret-value"


def test_tls_failure_is_retryable(monkeypatch, tmp_path):
    queue_mod = _reload_queue(monkeypatch, tmp_path)
    monkeypatch.setenv("CPA_MINT_WORKERS", "0")
    monkeypatch.setattr(
        queue_mod,
        "_run_mint_and_auth_push",
        lambda **_kwargs: {
            "ok": False,
            "status": "tls_hostname_mismatch",
            "error": "certificate subject mismatch",
        },
    )
    result = queue_mod._process_job(
        {
            "sso": "sso-value",
            "email": "person@example.test",
            "run_at": time.time() - 1,
            "flags": {"sso_g2": False, "auth_cpa": False, "auto_auth": True},
            "mint_mode": "pkce",
        }
    )

    assert result["ok"] is False
    assert result["status"] == "tls_hostname_mismatch"
    assert result["retryable"] is True


def test_terminal_metadata_does_not_contain_credentials(monkeypatch, tmp_path):
    _reload_queue(monkeypatch, tmp_path)
    import auth_queue_daemon

    daemon = importlib.reload(auth_queue_daemon)
    metadata = daemon._safe_result(
        {
            "job_id": "job-1",
            "email": "person@example.test",
            "password": "password-secret",
            "sso": "sso-secret-value",
        },
        {"ok": False, "status": "network_timeout", "error": "timeout"},
    )
    encoded = json.dumps(metadata)
    assert "person@example.test" not in encoded
    assert "password-secret" not in encoded
    assert "sso-secret-value" not in encoded
    assert metadata["email_hash"]


def test_daemon_worker_count_is_independent_of_late_runtime_config(monkeypatch, tmp_path):
    _reload_queue(monkeypatch, tmp_path)
    import auth_queue_daemon

    daemon = importlib.reload(auth_queue_daemon)
    monkeypatch.delenv("AUTH_QUEUE_WORKERS", raising=False)
    assert daemon._daemon_workers() == 1

    monkeypatch.setenv("AUTH_QUEUE_WORKERS", "3")
    assert daemon._daemon_workers() == 3

    monkeypatch.setenv("AUTH_QUEUE_WORKERS", "99")
    assert daemon._daemon_workers() == 4


def test_oauth_error_classification():
    from oauth_preflight import _classify_error

    assert (
        _classify_error(
            "curl: (60) SSL: no alternative certificate subject name matches target hostname"
        )
        == "tls_hostname_mismatch"
    )
    assert _classify_error("operation timed out") == "network_timeout"
