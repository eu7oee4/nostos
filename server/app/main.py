from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.api.routes import router as api_router
from app.config import settings

app = FastAPI(title="nostos", version="0.0.0")
app.include_router(api_router)

WEB_DIR = Path(__file__).resolve().parents[2] / "web"


@app.on_event("startup")
def _ensure_data_dir() -> None:
    Path(settings.data_dir).mkdir(parents=True, exist_ok=True)


@app.get("/")
def index():
    index_path = WEB_DIR / "index.html"
    if index_path.is_file():
        return FileResponse(index_path)
    return {"service": "nostos", "stage": "scaffold"}
