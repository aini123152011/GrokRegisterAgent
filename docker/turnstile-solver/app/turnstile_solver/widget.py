from __future__ import annotations

import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

from .config import SolverConfig
from .models import SolveOutcome, TaskSpec, TaskState


TOKEN_SELECTOR = 'input[name="cf-turnstile-response"], textarea[name="cf-turnstile-response"]'
CF_FRAME_PREFIX = "https://challenges.cloudflare.com/"
StateCallback = Callable[[TaskState], Awaitable[None]]


class WidgetError(RuntimeError):
    pass


class WidgetSolver:
    """Render and interact with one widget in an isolated browser context."""

    def __init__(self, config: SolverConfig) -> None:
        self.config = config

    async def solve(self, page: Any, spec: TaskSpec, on_state: StateCallback) -> SolveOutcome:
        started = time.monotonic()
        attempts = 0

        await on_state(TaskState.WAIT_AUTOMATIC)
        token, error = await self._poll_token(page, self.config.automatic_wait_ms)
        if token:
            return self._outcome(token, attempts, started)

        await on_state(TaskState.RENDER_WIDGET)
        await self._render(page, spec)

        await on_state(TaskState.WAIT_AUTOMATIC)
        token, error = await self._poll_token(page, self.config.automatic_wait_ms)
        if token:
            return self._outcome(token, attempts, started)

        await on_state(TaskState.INTERACT)
        for attempts in range(1, self.config.max_click_attempts + 1):
            clicked = await self._click_visible_frame(page)
            if not clicked:
                await page.wait_for_timeout(500)
            token, error = await self._poll_token(page, self.config.interaction_wait_ms)
            if token:
                return self._outcome(token, attempts, started)
            if error:
                # The widget can recover from transient errors after retry/refresh.
                await page.wait_for_timeout(750)

        detail = f" ({error})" if error else ""
        raise WidgetError(f"Turnstile returned no token after {attempts} interaction attempts{detail}")

    @staticmethod
    def _outcome(token: str, attempts: int, started: float) -> SolveOutcome:
        return SolveOutcome(
            token=token,
            attempts=attempts,
            elapsed_ms=int((time.monotonic() - started) * 1_000),
        )

    async def _render(self, page: Any, spec: TaskSpec) -> None:
        payload = {
            "sitekey": spec.sitekey,
            "action": spec.action or None,
            "cData": spec.cdata or None,
        }
        errors: list[str] = []
        for attempt in range(1, 4):
            try:
                wait_for_load = getattr(page, "wait_for_load_state", None)
                if callable(wait_for_load):
                    try:
                        await wait_for_load("domcontentloaded", timeout=5_000)
                    except Exception:
                        pass
                has_api = await page.evaluate(
                    "() => Boolean(window.turnstile && typeof window.turnstile.render === 'function')"
                )
                if not has_api:
                    await page.add_script_tag(
                        url="https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit"
                    )
                await page.wait_for_function(
                    "() => window.turnstile && typeof window.turnstile.render === 'function'",
                    timeout=15_000,
                )
                result = await page.evaluate(
                    """
                    (payload) => {
                      if (!window.turnstile || typeof window.turnstile.render !== 'function') {
                        return {ok: false, error: 'turnstile-api-missing'};
                      }
                  const old = document.getElementById('solver-turnstile-host');
                  if (old) old.remove();
                  const host = document.createElement('div');
                  host.id = 'solver-turnstile-host';
                  host.style.cssText = 'position:fixed;z-index:2147483647;left:24px;top:24px;min-width:320px;min-height:80px;background:#fff;padding:8px';
                  document.documentElement.appendChild(host);
                  window.__solverTurnstile = { token: '', error: '' };
                  const options = {
                    sitekey: payload.sitekey,
                    retry: 'auto',
                    'refresh-expired': 'auto',
                    callback: token => { window.__solverTurnstile.token = String(token || ''); },
                    'error-callback': code => {
                      window.__solverTurnstile.error = `widget-error:${String(code || 'unknown')}`;
                    },
                    'expired-callback': () => { window.__solverTurnstile.error = 'widget-expired'; },
                    'timeout-callback': () => { window.__solverTurnstile.error = 'widget-timeout'; }
                  };
                  if (payload.action) options.action = payload.action;
                  if (payload.cData) options.cData = payload.cData;
                      const widgetId = String(window.turnstile.render(host, options) || '');
                      return {ok: Boolean(widgetId), widgetId, error: widgetId ? '' : 'empty-widget-id'};
                    }
                    """,
                    payload,
                )
                if isinstance(result, dict) and result.get("ok") and result.get("widgetId"):
                    return
                # Keep compatibility with browser test doubles and older runtimes.
                if isinstance(result, str) and result:
                    return
                detail = str(result.get("error") or "render returned no widget id") if isinstance(result, dict) else "render returned no widget id"
                raise WidgetError(detail)
            except Exception as exc:
                errors.append(f"attempt {attempt}: {exc}")
                if attempt < 3:
                    await page.wait_for_timeout(750)
        raise WidgetError(f"failed to render Turnstile widget after 3 attempts: {'; '.join(errors)}")

    async def _poll_token(self, page: Any, timeout_ms: int) -> tuple[str, str]:
        deadline = time.monotonic() + timeout_ms / 1_000
        last_error = ""
        while time.monotonic() < deadline:
            token, error = await self._read_state(page)
            if token:
                return token, error
            last_error = error or last_error
            await page.wait_for_timeout(250)
        token, error = await self._read_state(page)
        return token, error or last_error

    @staticmethod
    async def _read_state(page: Any) -> tuple[str, str]:
        try:
            state = await page.evaluate(
                """
                (selector) => {
                  const direct = window.__solverTurnstile || {};
                  if (direct.token) return {token: String(direct.token), error: String(direct.error || '')};
                  for (const node of document.querySelectorAll(selector)) {
                    const value = String(node.value || '').trim();
                    if (value) return {token: value, error: String(direct.error || '')};
                  }
                  return {token: '', error: String(direct.error || '')};
                }
                """,
                TOKEN_SELECTOR,
            )
            if isinstance(state, dict):
                return str(state.get("token") or "").strip(), str(state.get("error") or "").strip()
        except Exception:
            # Navigation can replace the JS execution context while polling.
            pass
        return "", ""

    @staticmethod
    async def _click_visible_frame(page: Any) -> bool:
        frames = list(getattr(page, "frames", ()) or ())
        for frame in frames:
            if not str(getattr(frame, "url", "") or "").startswith(CF_FRAME_PREFIX):
                continue
            try:
                element = await frame.frame_element()
                box = await element.bounding_box()
                visible = await element.is_visible()
                if not visible or not box or box.get("width", 0) < 40 or box.get("height", 0) < 40:
                    continue
                x = float(box["x"]) + random.uniform(26, 30)
                y = float(box["y"]) + random.uniform(25, 29)
                await page.mouse.click(x, y, delay=random.randint(90, 180), button="left")
                return True
            except Exception:
                continue
        return False
