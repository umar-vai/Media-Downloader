from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from media_core.browser_resolver import *  # noqa: F401,F403

# Compatibility exports for legacy desktop diagnostics/tests.
from media_core.browser_resolver import _format_from_response, _looks_like_media  # noqa: F401
