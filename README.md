# nostos

> Tentative name. An AI companion that remembers you and reaches out. Self-hosted / BYOK.

**中文说明（更详细）：[README-zh.md](README-zh.md)**

**Companion, not assistant.** Stage: **min-chat** — talk in a loop. No long-term memory, no proactive reach-out, no nostools yet.

## Quick start (computer)

```bash
git clone https://github.com/eu7oee4/nostos.git
cd nostos
git fetch origin && git checkout feat/min-chat-loop   # until merged to main
cp .env.example .env   # set LLM_API_KEY
docker compose up --build
```

Open **http://localhost:8787** or **http://127.0.0.1:8787** (not `www.localhost.com`). Health: `/health`.

## Phone access

The server runs on your computer. On a phone, `localhost` is the phone — use LAN or a tunnel. **No auth yet**; treat public links as keys.

### 1. Same Wi‑Fi

1. Keep compose running; confirm `http://localhost:8787` works on the computer.
2. Find the LAN IP (Mac: `ipconfig getifaddr en0`).
3. On the phone (same Wi‑Fi, not guest): `http://<LAN-IP>:8787`.

### 2. Tunnel

Prefer **cloudflared quick tunnel** for a one-off try (fewer steps, no signup). Use **ngrok** if you already have an account / want its dashboard.

**cloudflared:**

```bash
brew install cloudflare/cloudflare/cloudflared
# nostos already on :8787
cloudflared tunnel --url http://localhost:8787
# open the printed https://….trycloudflare.com on your phone
```

**ngrok:**

```bash
brew install ngrok/ngrok/ngrok
ngrok config add-authtoken <token>   # once, from ngrok dashboard
ngrok http 8787
```

Do not raw-port-forward 8787 to the open internet.

See **README-zh.md** for full step-by-step (Chinese).

## What works (min-chat)

- `GET /health` / `GET /messages` / `POST /chat`
- SQLite at `data/nostos.sqlite`

## Not yet

Memory, preferences, alarms, nostools, SSE, multi-user auth.

## Docs

- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [PLAN_companion.md](docs/PLAN_companion.md)

## License

MIT
