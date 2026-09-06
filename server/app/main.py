from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.routes import router as api_router
from app.config import settings
from app.db import init_db
from app.memory.store import memories_dir

app = FastAPI(title="nostos", version="0.2.0-minmemory")
app.include_router(api_router)

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


@app.on_event("startup")
async def _startup() -> None:
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)
    memories_dir(settings.user_id)  # ensure data/memories/<user_id>/
    await init_db()


@app.get("/")
def index():
    index_path = WEB_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    return {"service": "nostos", "stage": "min-memory"}
