from __future__ import annotations

import turnstile_solver_client as client


def test_task_payload_forwards_proxy_action_and_cdata():
    task = client._task_payload(
        "https://example.com/signup",
        "site-key",
        proxy="http://user:pass@proxy:8080",
        action="signup",
        cdata="opaque",
    )
    assert task["type"] == "TurnstileTask"
    assert task["proxy"].startswith("http://")
    assert task["action"] == "signup"
    assert task["cData"] == "opaque"


def test_terminal_solver_error_stops_polling(monkeypatch):
    responses = [
        {"errorId": 0, "taskId": "task-1"},
        {
            "errorId": 1,
            "status": "failed",
            "errorCode": "ERROR_TASK_FAILED",
            "errorDescription": "fixture failure",
        },
    ]
    calls = []

    def fake_http(*args, **kwargs):
        calls.append((args, kwargs))
        return responses.pop(0)

    monkeypatch.setattr(client, "_http_json", fake_http)
    result = client._solve_provider(
        "local",
        "https://example.com",
        "site-key",
        base_url="http://solver",
        client_key="secret",
        proxy="",
        action="",
        cdata="",
        max_wait=30,
        log=lambda _message: None,
    )
    assert result.status == "failed"
    assert result.error_code == "ERROR_TASK_FAILED"
    assert len(calls) == 2


def test_probe_uses_health_endpoint(monkeypatch):
    seen = []

    def fake_http(method, url, **_kwargs):
        seen.append((method, url))
        return {"ok": True, "status": "ready", "backend": "chromium", "queueDepth": 0}

    monkeypatch.setattr(client, "_http_json", fake_http)
    result = client.probe_solver("http://solver")
    assert result["ok"] is True
    assert seen == [("GET", "http://solver/health")]


def test_client_wait_never_expires_before_local_task(monkeypatch):
    monkeypatch.setenv("TURNSTILE_TASK_TIMEOUT", "90")
    monkeypatch.setenv("TURNSTILE_CLIENT_WAIT_TIMEOUT", "30")

    assert client.solver_task_timeout({}) == 90
    assert client.solver_client_wait_timeout({}) == 105


def test_client_wait_honors_larger_explicit_deadline(monkeypatch):
    monkeypatch.setenv("TURNSTILE_TASK_TIMEOUT", "120")
    monkeypatch.setenv("TURNSTILE_CLIENT_WAIT_TIMEOUT", "160")

    assert client.solver_client_wait_timeout({}) == 160
