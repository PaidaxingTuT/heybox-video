import asyncio

from fastapi import APIRouter, HTTPException

from config import ALLOWED_DOMAIN
from scraper import HeyboxScraper

router = APIRouter()


@router.get("/api/parse")
async def parse_api(url: str):
    if not url:
        raise HTTPException(status_code=400, detail="链接不能为空")

    if ALLOWED_DOMAIN not in url:
        raise HTTPException(status_code=400, detail="请粘贴有效的小黑盒链接")

    try:
        video_url = await asyncio.to_thread(HeyboxScraper.scrape, url)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"解析出错: {e}")

    if not video_url:
        raise HTTPException(status_code=404, detail="未抓取到视频，请检查该帖子是否包含视频")

    return {"video_url": video_url}
