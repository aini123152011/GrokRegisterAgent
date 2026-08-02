from __future__ import annotations

import sys
from pathlib import Path


APP_DIR = Path(__file__).resolve().parents[1]
REGISTER_DIR = Path(__file__).resolve().parents[4] / "register"
for path in (APP_DIR, REGISTER_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
