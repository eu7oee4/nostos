"""SQLite access stub. Every row must carry user_id."""

from pathlib import Path

from app.config import settings

DB_NAME = "nostos.sqlite"


def db_path() -> Path:
    root = Path(settings.data_dir)
    root.mkdir(parents=True, exist_ok=True)
    return root / DB_NAME


# Schema sketch (not applied yet):
# messages(id, user_id, role, content, created_at, ...)
# memories meta in index.json under data/memories/
# alarms(id, user_id, fire_at, payload, ...)
