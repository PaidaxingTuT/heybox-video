# Heybox Video

小黑盒帖子视频直链解析工具。粘贴分享链接，一键获取视频直链，支持在线预览和下载。

## 技术栈

| 层 | 技术 |
| --- | --- |
| 前端 | 原生 HTML / CSS / JS |
| 后端 | Python 3.12 + FastAPI |
| 轻量解析 | requests |
| 浏览器回退 | nodriver / Playwright |
| 包管理 | uv |

## 快速开始

最省事的方式是在项目根目录直接运行：

```bat
start.bat
```

`start.bat` 的行为是：

- 优先使用 `uv`
- 自动执行 `uv sync`
- 自动启动后端服务
- 如果系统没有 `uv`，会自动回退到 `python` / `pip`
- 回退时会自动创建 `backend/.venv`、安装依赖并启动服务

如果你想手动启动，也可以用下面的命令行方式：

```bash
cd backend
uv sync
uv run start
```

服务默认运行在 `http://127.0.0.1:9003`。

浏览器里打开后，粘贴小黑盒 App 的帖子分享链接即可解析。

## 当前解析流程

后端采用“两段式”解析：

1. 先走轻量 HTTP fast path，不启动浏览器。
2. 如果 fast path 已经拿到视频直链，直接返回结果。
3. 如果 fast path 没抓到视频，才回退到浏览器 worker。
4. 默认浏览器后端是 `nodriver`，并且会先尝试无头模式。
5. 只有遇到验证码、人工验证或类似风控场景时，才会升级为有头浏览器窗口。

这意味着：

- 正常可解析的链接，不会一上来就弹浏览器。
- 同一个链接是否触发风控是实时变化的，不能永久假设某条链接一定安全。
- 浏览器阶段拿到的 cookie / storage 会回写到本地会话文件，后续 fast path 会优先复用。

## 安装说明

项目当前依赖：

- `fastapi`
- `uvicorn`
- `requests`
- `nodriver`
- `playwright`

如果你是新增依赖，推荐使用：

```bash
uv add <package>
```

## 配置

可通过环境变量覆盖以下配置：

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `HEYBOX_HOST` | `127.0.0.1` | 监听地址 |
| `HEYBOX_PORT` | `9003` | 监听端口 |
| `HEYBOX_BROWSER_BACKEND` | `nodriver` | 浏览器回退后端，可选 `nodriver` / `playwright` |

其余参数位于 [backend/config.py](D:/Code/Python/heybox_video/backend/config.py)，例如：

- `PLAYWRIGHT_HEADLESS`
- `PLAYWRIGHT_TIMEOUT`
- `CAPTCHA_MANUAL_TIMEOUT`
- `CAPTCHA_COOLDOWN`

## 验证码与风控

小黑盒的风控不是固定的，同一个帖子可能现在能解析，稍后又触发验证。

当前策略是：

- fast path 先尝试，不命中就回退浏览器。
- 浏览器优先无头运行，尽量不打扰使用。
- 检测到验证码时，才打开可见窗口等待人工处理。
- 验证失败或超时后，会进入冷却期，避免持续撞风控。

## 元数据策略

返回结构保持兼容，接口仍然返回：

- `video_url`
- `meta.title`
- `meta.description`
- `captcha_required`
- `cooldown`

元数据提取规则做过收敛：

- 有标题时优先用标题。
- 没有标题时回退到简介。
- 不再把评论区内容当作帖子简介。

## 项目结构

```text
heybox_video/
├── backend/
│   ├── app.py
│   ├── config.py
│   ├── main.py
│   ├── scraper.py
│   ├── heybox_fast.py
│   ├── heybox_browser_nodriver.py
│   ├── heybox_browser_playwright.py
│   ├── routes/
│   │   └── parse.py
│   ├── pyproject.toml
│   └── requirements.txt
├── frontend/
│   ├── index.html
│   ├── style.css
│   └── script.js
└── README.md
```

## 说明

- 仅支持 `xiaoheihe.cn` 相关帖子分享链接。
- 服务启动时只检查依赖可用性，不会主动拉起浏览器。
- 浏览器仅作为回退链路使用，不是所有请求都会经过浏览器。
- 视频资源版权归小黑盒及原作者所有，请勿用于商业用途。

## License

MIT
