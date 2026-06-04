import asyncio

from fastapi import APIRouter, HTTPException

from config import ALLOWED_DOMAIN
from scraper import CaptchaRequiredError, HeyboxScraper

router = APIRouter()


@router.get("/api/parse")
async def parse_api(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="链接不能为空")

    if ALLOWED_DOMAIN not in url:
        raise HTTPException(status_code=400, detail="请粘贴有效的小黑盒链接")

    try:
        result = await asyncio.to_thread(HeyboxScraper.scrape, url)
    except CaptchaRequiredError:
        return {
            "captcha_required": True,
            "message": "返回验证码，无法自动解析该帖子，请手动下载",
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
