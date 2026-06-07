import os

HOST = os.environ.get("HEYBOX_HOST", "127.0.0.1")
PORT = int(os.environ.get("HEYBOX_PORT", "9003"))
PLAYWRIGHT_TIMEOUT = 3000  # ms
ALLOWED_DOMAIN = "xiaoheihe.cn"
PLAYWRIGHT_HEADLESS = os.environ.get("HEYBOX_HEADLESS", "false").lower() in ("1", "true", "yes")
PLAYWRIGHT_USER_DATA_DIR = ".heybox_browser"
BROWSER_BACKEND = os.environ.get("HEYBOX_BROWSER_BACKEND", "nodriver").strip().lower()
CAPTCHA_MANUAL_TIMEOUT = 60000  # ms
# 命中验证码/操作频繁后进入冷却，冷却期内的请求直接退避，不再撞限流
CAPTCHA_COOLDOWN = 90  # s
