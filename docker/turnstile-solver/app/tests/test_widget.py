from __future__ import annotations

import asyncio

from turnstile_solver.config import SolverConfig
from turnstile_solver.models import TaskSpec, TaskState
from turnstile_solver.widget import WidgetSolver


class FakeElement:
    def __init__(self, visible=True, width=300):
        self.visible = visible
        self.width = width

    async def bounding_box(self):
        return {"x": 100, "y": 100, "width": self.width, "height": 65}

    async def is_visible(self):
        return self.visible


class FakeFrame:
    url = "https://challenges.cloudflare.com/cdn-cgi/challenge-platform/test"

    def __init__(self, element=None):
        self.element = element or FakeElement()

    async def frame_element(self):
        return self.element


class FakeMouse:
    def __init__(self, page):
        self.page = page

    async def click(self, *_args, **_kwargs):
        self.page.token = "fixture-token"


class FakePage:
    def __init__(self, frame=None):
        self.frames = [frame or FakeFrame()]
        self.mouse = FakeMouse(self)
        self.token = ""
        self.rendered = False

    async def evaluate(self, expression, _argument=None):
        if "window.turnstile.render" in expression:
            self.rendered = True
            return {"ok": True, "widgetId": "widget-id", "error": ""}
        if "typeof window.turnstile" in expression:
            return True
        if "document.querySelectorAll" in expression:
            return {"token": self.token, "error": ""}
        return None

    async def add_script_tag(self, **_kwargs):
        return None

    async def wait_for_function(self, *_args, **_kwargs):
        return None

    async def wait_for_timeout(self, _milliseconds):
        await asyncio.sleep(0)


def test_widget_uses_payload_render_and_requires_a_real_click_result():
    async def scenario():
        cfg = SolverConfig(automatic_wait_ms=1, interaction_wait_ms=1, max_click_attempts=2)
        page = FakePage()
        states = []

        async def state(value):
            states.append(value)

        result = await WidgetSolver(cfg).solve(
            page,
            TaskSpec("https://example.com", "site-key", action="signup", cdata="opaque"),
            state,
        )
        assert result.token == "fixture-token"
        assert result.attempts == 1
        assert page.rendered is True
        assert TaskState.RENDER_WIDGET in states
        assert TaskState.INTERACT in states

    asyncio.run(scenario())


def test_hidden_or_tiny_frame_is_not_reported_as_clicked():
    async def scenario():
        page = FakePage(FakeFrame(FakeElement(visible=True, width=1)))
        assert await WidgetSolver._click_visible_frame(page) is False
        assert page.token == ""

    asyncio.run(scenario())
