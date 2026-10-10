from __future__ import annotations

# Compatibility entry point. The Hybrid/PWA Local Core is now supervised by
# hybrid_core.agent so old developer commands keep the same behavior.
from .agent import main


if __name__ == "__main__":
    raise SystemExit(main())
