# min-chat PR notes

## Scope

Minimal talk loop only:

1. SQLite `messages` with `user_id`
2. `POST /chat` → DeepSeek-compatible `/chat/completions` (non-streaming)
3. Simple `web/index.html` UI
4. No memory / alarm / nostools wiring

## How to verify

1. `cp .env.example .env` and set `LLM_API_KEY`
2. `docker compose up --build`
3. Open `/`, send a message; reload and confirm history persists
4. `/health` shows `has_key: true` and `stage: min-chat`
