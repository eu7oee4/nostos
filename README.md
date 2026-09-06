# nostos

> 暂定名。AI 伙伴：记得你，也会来找你。自托管 / BYOK。

**Companion, not assistant.** MVP proves whether people stay because it *remembers* and *reaches out* — not because chat is fun.

## Quick start

```bash
git clone https://github.com/eu7oee4/nostos.git
cd nostos
cp .env.example .env   # set LLM_API_KEY
docker compose up
```

Then open http://localhost:8787/health (scaffold: health only for now).

## Layout

| Path | Role |
|------|------|
| `server/` | FastAPI + SQLite + scheduler |
| `server/app/nostools/` | Antenna layer registry (暂定名; not a plugin store) |
| `web/` | Minimal static UI placeholder |
| `docs/` | PLAN + architecture |
| `eval/` | Model-selection eval (later) |
| `data/` | Runtime: sqlite + memories (gitignored) |

## Docs

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — locked decisions
- [PLAN_companion.md](docs/PLAN_companion.md) — design (from cassette)

## License

MIT
