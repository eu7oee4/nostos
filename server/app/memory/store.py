from pathlib import Path

from app.config import settings


def memories_dir(user_id: str | None = None) -> Path:
    uid = user_id or settings.user_id
    path = Path(settings.data_dir) / "memories" / uid
    path.mkdir(parents=True, exist_ok=True)
    return path
