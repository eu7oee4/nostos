# nostos

> 暂定名。AI 伙伴：记得你，也会来找你。自托管 / BYOK。

**Companion, not assistant.** This branch stage: **min-chat** — talk in a loop. No long-term memory, no proactive reach-out, no nostools yet.

## Quick start

```bash
git clone https://github.com/eu7oee4/nostos.git
cd nostos
cp .env.example .env   # set LLM_API_KEY (DeepSeek by default)
docker compose up --build
```

Open http://localhost:8787 — type and send. Health: http://localhost:8787/health

Without Docker:

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd ..
export PYTHONPATH=server DATA_DIR=./data
# load .env yourself or export LLM_API_KEY
uvicorn app.main:app --app-dir server --reload --port 8787
```

## What works (min-chat)

- `GET /health`
- `GET /messages` — history for `USER_ID` (default `local`)
- `POST /chat` `{"content":"..."}` — persist user turn → call LLM → persist assistant turn
- SQLite at `data/nostos.sqlite`, server-stamped timestamps

## Not yet

- Memory md / recall, preferences, alarms, nostools, SSE streaming, multi-user auth

## Docs

- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [PLAN_companion.md](docs/PLAN_companion.md) (link to cassette until full copy lands)

## License

MIT
