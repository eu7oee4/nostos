# nostos

> Tentative name. An AI companion that remembers you and reaches out. Self-hosted / BYOK.

**中文说明（更详细）：[README-zh.md](README-zh.md)**

**Companion, not assistant.** Stage: **min-chat**. No long-term memory, proactive reach-out, or nostools yet.

## Quick start (computer)

```bash
git clone https://github.com/eu7oee4/nostos.git
cd nostos
git fetch origin && git checkout feat/min-chat-loop   # until merged
cp .env.example .env   # set LLM_API_KEY
docker compose up --build
```

Open **http://localhost:8787** or **http://127.0.0.1:8787** (not `www.localhost.com`).

Full step-by-step: **README-zh.md**.

## Phone access

Server runs on your computer. Pick by scenario:

1. **Reach the computer’s IP** (no public internet required)  
   - **1A Tailscale** — leave the Mac running; chat away from home via `http://<tailscale-ip>:8787`. On phones, Tailscale often conflicts with other VPNs/proxies; on Mac you can usually keep a proxy if you turn off **Use Tailscale DNS settings**.  
   - **1B Same Wi‑Fi** — simplest at home: `http://<LAN-IP>:8787`.

2. **Temporary public tunnel** (link = key; no auth yet)  
   - **2A cloudflared** (preferred for a quick try): `cloudflared tunnel --url http://localhost:8787`  
   - **2B ngrok**: `ngrok http 8787`

3. **Deploy your own cloud server** — the real “open and use” path; **not shipped yet**.

Do not raw-port-forward 8787 to the open internet.

## What works / not yet

See README-zh.md.

## Docs

- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [PLAN_companion.md](docs/PLAN_companion.md)

## License

MIT
