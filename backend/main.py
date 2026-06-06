import asyncio
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn
from app import create_app
from config import BROWSER_BACKEND, HOST, PORT


def ensure_chromium():
    """Ensure the configured browser backend is importable without launching a browser."""
    if BROWSER_BACKEND == "nodriver":
        try:
            import nodriver  # noqa: F401
        except ImportError as exc:
            raise RuntimeError("nodriver is not installed. Run `uv add nodriver` first.") from exc
        return

    try:
        import playwright  # noqa: F401
    except ImportError as exc:
        raise RuntimeError("Playwright is not installed. Run `uv sync` first.") from exc


app = create_app()


def main():
    """Start the FastAPI server."""
    ensure_chromium()
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
