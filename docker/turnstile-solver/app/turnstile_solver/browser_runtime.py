from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .config import SolverConfig
from .models import SolveOutcome, TaskSpec, TaskState
from .proxy import parse_proxy
from .widget import WidgetSolver


logger = logging.getLogger(__name__)
StateCallback = Callable[[TaskState], Awaitable[None]]


class BrowserRuntime:
    """Own the browser processes and lend one process to each queued task."""

    def __init__(self, config: SolverConfig) -> None:
        self.config = config
        self._driver: Any = None
        self._browsers: asyncio.Queue[Any] = asyncio.Queue(maxsize=config.workers)
        self._all: list[Any] = []
        self._started = False
        self._start_lock = asyncio.Lock()
        self.widget = WidgetSolver(config)

    async def start(self) -> None:
        async with self._start_lock:
            if self._started:
                return
            self._driver = await self._start_driver()
            try:
                for _ in range(self.config.workers):
                    browser = await self._launch_browser()
                    self._all.append(browser)
                    await self._browsers.put(browser)
            except Exception:
                await self.close()
                raise
            self._started = True
            logger.info("browser runtime ready backend=%s workers=%s", self.config.backend, self.config.workers)

    async def close(self) -> None:
        while not self._browsers.empty():
            try:
                self._browsers.get_nowait()
            except asyncio.QueueEmpty:
                break
        for browser in self._all:
            await self._close_object(browser)
        self._all.clear()
        if self._driver is not None:
            await self._close_object(self._driver, "stop")
        self._driver = None
        self._started = False

    async def solve(self, spec: TaskSpec, on_state: StateCallback) -> SolveOutcome:
        if not self._started:
            await self.start()
        await on_state(TaskState.ACQUIRE_BROWSER)
        browser = await self._browsers.get()
        context = None
        healthy = True
        try:
            await on_state(TaskState.CREATE_CONTEXT)
            # Camoufox generates a coherent Firefox fingerprint (screen,
            # viewport, locale, fonts) at browser launch. Overriding those
            # values per context breaks that coherence. Chromium does not
            # provide a generated fingerprint, so use a realistic desktop
            # context there.
            options: dict[str, Any] = {}
            if self.config.backend != "camoufox":
                options.update(
                    {
                        "viewport": {"width": 1365, "height": 768},
                        "screen": {"width": 1365, "height": 768},
                        "locale": "en-US",
                        "color_scheme": "light",
                    }
                )
            if spec.proxy:
                if not self.config.proxy_support:
                    raise RuntimeError("per-task proxy support is disabled")
                proxy = parse_proxy(spec.proxy)
                if self.config.backend == "camoufox" and proxy and proxy["server"].startswith("socks") and proxy.get("username"):
                    raise RuntimeError("Camoufox does not support authenticated SOCKS proxies; use HTTP CONNECT")
                options["proxy"] = proxy
            context = await browser.new_context(**options)
            page = await context.new_page()
            page.set_default_timeout(self.config.navigation_timeout_ms)
            page.set_default_navigation_timeout(self.config.navigation_timeout_ms)

            await on_state(TaskState.NAVIGATE)
            await page.goto(spec.url, wait_until="domcontentloaded", timeout=self.config.navigation_timeout_ms)
            return await self.widget.solve(page, spec, on_state)
        except Exception:
            healthy = self._is_connected(browser)
            raise
        finally:
            if context is not None:
                await self._close_object(context)
            if healthy and self._is_connected(browser):
                await self._browsers.put(browser)
            else:
                await self._replace_browser(browser)

    async def _start_driver(self) -> Any:
        if self.config.backend == "camoufox":
            from playwright.async_api import async_playwright

            return await async_playwright().start()
        if self.config.backend not in {"chromium", "patchright", "chrome", "msedge"}:
            raise RuntimeError(f"unsupported browser backend: {self.config.backend}")
        from patchright.async_api import async_playwright

        return await async_playwright().start()

    async def _launch_browser(self) -> Any:
        if self.config.backend == "camoufox":
            from camoufox.async_api import AsyncNewBrowser

            # Native Firefox headless is readily classified by the target.
            # A virtual display keeps the container non-interactive while
            # exercising the normal headed rendering path.
            camoufox_headless: bool | str = "virtual" if self.config.headless else False
            return await AsyncNewBrowser(
                self._driver,
                headless=camoufox_headless,
                humanize=True,
                window=(1365, 768),
            )
        kwargs: dict[str, Any] = {
            "headless": self.config.headless,
            "args": ["--disable-dev-shm-usage"],
        }
        if self.config.backend in {"chrome", "msedge"}:
            kwargs["channel"] = self.config.backend
        return await self._driver.chromium.launch(**kwargs)

    async def _replace_browser(self, browser: Any) -> None:
        await self._close_object(browser)
        try:
            self._all.remove(browser)
        except ValueError:
            pass
        if self._driver is None:
            return
        replacement = await self._launch_browser()
        self._all.append(replacement)
        await self._browsers.put(replacement)

    @staticmethod
    def _is_connected(browser: Any) -> bool:
        fn = getattr(browser, "is_connected", None)
        try:
            return bool(fn()) if callable(fn) else True
        except Exception:
            return False

    @staticmethod
    async def _close_object(obj: Any, method: str = "close") -> None:
        fn = getattr(obj, method, None)
        if not callable(fn):
            return
        try:
            result = fn()
            if inspect.isawaitable(result):
                await result
        except Exception:
            logger.debug("close failed", exc_info=True)
