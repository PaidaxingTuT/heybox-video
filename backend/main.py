import sys
import asyncio

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import uvicorn
from app import create_app
from config import HOST, PORT


def ensure_chromium():
    """第一次运行时自动安装 Playwright Chromium。"""
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            p.chromium.launch(headless=True).close()
    except Exception:
        import subprocess
        print("🔧 首次运行，正在安装 Chromium 浏览器…")
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
        print("Chromium 安装完成")


app = create_app()


def main():
    """入口：装 Chromium → 启动服务。"""
    ensure_chromium()
    uvicorn.run(app, host=HOST, port=PORT)


if __name__ == "__main__":
    main()
