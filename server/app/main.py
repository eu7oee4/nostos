from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.routes import router as api_router
from app.config import settings
from app.db import init_db
from app.memory.store import memories_dir
from app.schedule import start_scheduler, stop_scheduler

app = FastAPI(title="nostos", version="0.4.0-random-wake")
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


@app.get("/")
def index():
    index_path = WEB_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    return {"service": "nostos", "stage": "random-wake"}


@app.get("/settings")
def settings_page():
    path = WEB_DIR / "settings.html"
    if path.is_file():
        return FileResponse(path)
    return {"detail": "settings UI missing"}
