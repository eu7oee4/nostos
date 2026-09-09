import logging
import os
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.api.routes import router as api_router
from app.config import settings
from app.db import init_db
from app.memory.store import memories_dir
from app.schedule import start_scheduler, stop_scheduler

# uvicorn 的 LOGGING_CONFIG 只给 uvicorn.* 配 handler，root 一个都没有，而 root
# 默认 WARNING —— 不配这一下，app 侧所有 log.info 全被丢掉，包括 PLAN §15 要求
# 「第一天就进日志」的 usage cache 字段。实测：改之前 nostos.llm / nostos.wake
# 一条都不出现。
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(levelname)s:     %(name)s %(message)s",
)

app = FastAPI(title="nostos", version="0.3.0-minwake")
app.include_router(api_router)

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


@app.on_event("startup")
async def _startup() -> None:
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    memories_dir(settings.user_id)
    await init_db()
    await start_scheduler()


@app.on_event("shutdown")
async def _shutdown() -> None:
    await stop_scheduler()


# Service Worker 必须从根路径提供（scope 决定它能管哪些页面），而且 MIME 必须是
# JS。所以这两个是显式路由，不 mount StaticFiles —— web/ 里将来会有不该外露的东西。
@app.get("/sw.js")
def service_worker():
    return FileResponse(WEB_DIR / "sw.js", media_type="application/javascript")


@app.get("/manifest.json")
def manifest():
    return FileResponse(WEB_DIR / "manifest.json", media_type="application/manifest+json")


@app.get("/icon-{size}.png")
def icon(size: int):
    path = WEB_DIR / f"icon-{size}.png"
    if not path.is_file():
        raise HTTPException(404, "not found")
    return FileResponse(path, media_type="image/png")


@app.get("/")
def index():
    index_path = WEB_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    return {"service": "nostos", "stage": "min-wake"}
