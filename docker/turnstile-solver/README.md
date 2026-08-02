# Turnstile solver service

This directory contains the optional, isolated solver used by
GrokRegisterAgent. Chromium/Patchright is the default backend; Camoufox can be
selected with `BROWSER_TYPE=camoufox`.

## Start with Docker Compose

```bash
cd docker
cp .env.example .env
# Set TURNSTILE_SOLVER_ENABLED=1 in .env.
docker compose --profile solver up -d --build
curl http://127.0.0.1:5072/health
```

The service supports `POST /createTask` and `POST /getTaskResult`, plus the
legacy `GET /turnstile` and `GET /result` routes. Set `TURNSTILE_API_KEY` on
both containers to authenticate every task route. `/health` intentionally
returns only operational counters.

Important settings:

- `THREAD`: browser worker count (1-16).
- `TURNSTILE_QUEUE_SIZE`: maximum pending tasks.
- `TURNSTILE_TASK_TIMEOUT`: hard deadline per task.
- `TURNSTILE_ALLOWED_HOSTS`: comma-separated exact hosts or `*.example.com`.
- `TURNSTILE_ALLOW_PRIVATE_TARGETS=1`: only for local fixtures.
- `TURNSTILE_HEADED=1`: show the browser when using `start.sh` outside Docker.

## Tests

```bash
python -m pytest app/tests -q
RUN_TURNSTILE_INTEGRATION=1 python -m pytest app/tests/test_browser_fixture.py -q -s
```

The integration test serves a local page using Cloudflare's documented
always-pass test sitekey; it does not submit a real registration.
