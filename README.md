# Heybox Video

小黑盒帖子视频直链解析工具。粘贴帖子链接，一键获取视频直链，支持在线预览和下载。

## 技术栈

| 层     | 技术                           |
| ------ | ------------------------------ |
| 前端   | 原生 HTML/CSS/JS               |
| 后端   | Python 3.12 + FastAPI          |
| 抓取   | Playwright (Chromium headless) |
| 包管理 | uv                             |

## 快速开始

```bash
cd backend
uv sync
uv run start
```

首次运行会自动安装 Chromium 浏览器（约 150MB），之后直接启动。

服务跑在 `http://127.0.0.1:8002`，浏览器打开，粘贴小黑盒 App 的帖子**分享链接**即可解析视频。

## 项目结构

```
heybox_video/
├── backend/
│   ├── app.py          # FastAPI 应用
│   ├── config.py       # 配置
│   ├── main.py         # 入口（自动装 Chromium）
│   ├── scraper.py      # Playwright 视频抓取
│   ├── routes/
│   │   └── parse.py    # /api/parse 路由
│   ├── pyproject.toml  # Python 项目配置
│   └── requirements.txt # pip 兼容
├── frontend/
│   └── index.html      # 前端页面
└── README.md
```

## 说明

- 仅支持 `api.xiaoheihe.cn` 域名的帖子分享链接
- Playwright headless 运行，首次启动自动下载 Chromium，无需手动安装
- 视频资源版权归小黑盒及原作者所有，请勿用于商业用途

## License

MIT
