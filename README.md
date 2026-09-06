# nostos

> Tentative name. An AI companion that remembers you and reaches out. Self-hosted / BYOK.

**中文说明：[README-zh.md](README-zh.md)**

**Companion, not assistant.** This branch stage: **min-chat** — talk in a loop. No long-term memory, no proactive reach-out, no nostools yet.

## Quick start

```bash
git clone https://github.com/eu7oee4/nostos.git
cd nostos
git checkout feat/min-chat-loop   # until merged
cp .env.example .env   # set LLM_API_KEY (DeepSeek by default)
docker compose up --build
```

On this machine open **http://localhost:8787** (not `www.localhost.com`). Health: http://localhost:8787/health

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

## Chat from your phone

The server runs on your computer. On a phone, `localhost` means the phone itself — use one of these instead.

### 1. Same Wi‑Fi (quickest)

1. Keep `docker compose up` running on the computer.
2. Find the computer’s LAN IP (examples: Mac `ipconfig getifaddr en0`, or System Settings → Network).
3. On the phone browser open `http://<LAN-IP>:8787` (e.g. `http://192.168.1.23:8787`).

Tips: phone and computer must be on the same Wi‑Fi (not guest/AP isolation). If it fails, check the Mac firewall allowing Docker/Python on port 8787. **Only for trusted networks** — there is no login yet.

### 2. Tunnel (different network)

Keep the app running locally, then expose it with a tunnel and open the HTTPS URL on your phone:

- [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/) (`cloudflared tunnel ...`)
- [ngrok](https://ngrok.com/) (`ngrok http 8787`)
- [Tailscale Funnel](https://tailscale.com/kb/1223/funnel) if you already use Tailscale

Treat the public link like a house key — don’t share it. Still no auth in min-chat.

Do **not** port-forward 8787 to the open internet without protection.

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
