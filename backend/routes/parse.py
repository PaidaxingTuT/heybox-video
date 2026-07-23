import asyncio
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException

from config import ALLOWED_DOMAIN
from scraper import CaptchaRequiredError, CooldownError, HeyboxScraper

router = APIRouter()


def is_bbs_post_url(url: str) -> bool:
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()
    return (
        parsed.scheme in {"http", "https"}
        and (hostname == ALLOWED_DOMAIN or hostname.endswith(f".{ALLOWED_DOMAIN}"))
        and "bbs" in parsed.path.split("/")
    )


@router.get("/api/parse")
async def parse_api(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="链接不能为空")

    if not is_bbs_post_url(url):
        raise HTTPException(status_code=400, detail="请粘贴有效的小黑盒链接")

    try:
        result = await asyncio.to_thread(HeyboxScraper.scrape, url)
    except CooldownError as e:
        return {
            "captcha_required": True,
            "cooldown": e.remaining,
            "message": f"操作过于频繁，已进入冷却，请 {e.remaining} 秒后重试或手动下载",
            "url": url,
        }
    except CaptchaRequiredError:
        return {
            "captcha_required": True,
            "message": "返回验证码或操作过于频繁，无法自动解析该帖子，请稍后重试或手动下载",
            "url": url,
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析出错: {e}")

    if not result:
        raise HTTPException(status_code=404, detail="未抓取到视频，请检查该帖子是否包含视频")

    meta = result.get("meta") or {}
    result["meta"] = {
        key: meta[key]
        for key in ("title", "description")
        if meta.get(key)
    }

    return result
