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

1. **Reach your machine’s IP** (no public internet required)  
   - **1A Tailscale** — Mac stays on; chat via `http://<tailscale-ip>:8787`. Phones often can’t run Tailscale + another VPN at once; on Mac, turn off **Use Tailscale DNS settings** to keep a system proxy.  
   - **1B Same Wi‑Fi** — `http://<LAN-IP>:8787` at home.

2. **Temporary public tunnel** (link = key; no auth)  
   - **2A cloudflared** (quick try): `cloudflared tunnel --url http://localhost:8787`  
   - **2B ngrok**: `ngrok http 8787`

3. **DIY cloud VPS** — **you** rent a server, clone this repo, run Compose, put HTTPS in front. This is **not** a hosted “open and use” product from the maintainers. Outline only for now (see README-zh.md); no one-click installer yet. Don’t expose bare `:8787` without some door (auth / Tailscale-only / etc.).

Do not raw-port-forward home `8787` to the open internet.

## Docs

- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [PLAN_companion.md](docs/PLAN_companion.md)

## License

MIT
