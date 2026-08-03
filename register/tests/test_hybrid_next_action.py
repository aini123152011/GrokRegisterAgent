from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from browser.token_harvester import BrowserTokenSession  # noqa: E402


def test_scrape_next_action_prefers_runtime_and_form_metadata():
    action = "a" * 40

    class FakePage:
        def run_js(self, source):
            assert "__hybrid_next_actions" in source
            assert "$$FORM_ACTION" in source
            assert "$ACTION_ID_" in source
            return action

    runtime = types.ModuleType("grok_register_ttk")
    runtime._get_page = lambda: FakePage()

    with patch.dict(sys.modules, {"grok_register_ttk": runtime}):
        assert BrowserTokenSession().scrape_next_action() == action

