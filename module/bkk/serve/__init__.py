"""HTTP server exposing BKK bundle content, search, and maintenance.

Public entry points: :func:`create_app` (returns a FastAPI app), and
:class:`ServeConfig` (loaded from defaults / env / CLI flags).

Importing this module requires the ``serve`` optional extra:
``pip install bkk[serve]``. Without it, FastAPI/uvicorn are missing and the
import below raises a clear error pointing at the install command.
"""

from __future__ import annotations

try:
    from .app import create_app
except ImportError as exc:  # pragma: no cover - exercised only when extras missing
    raise ImportError(
        "bkk.serve requires the 'serve' optional dependency group; "
        "install with: pip install 'bkk[serve]'"
    ) from exc

from .config import ServeConfig

__all__ = ["create_app", "ServeConfig"]
