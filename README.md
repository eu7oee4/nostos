# nostos

> Tentative name. An AI companion that remembers you and reaches out. Self-hosted / BYOK.

**中文说明（更详细）：[README-zh.md](README-zh.md)**

**Companion, not assistant.** Stage: **min-wake** — it talks, remembers you in markdown files, and can come find you on its own.

## Quick start (computer)

```bash
git clone https://github.com/eu7oee4/nostos.git
cd nostos
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

## What works today

- `GET /health`, `GET /messages`, `POST /chat` — the talk loop, SQLite-backed
- `GET /memories`, `GET /memories/{name}` — durable memories as markdown on disk;
  the model reads and writes them through tool_use
- `GET /wakes`, `POST /wakes` — proactive wakes ("it comes to find you").
  **Off by default**: set `PROACTIVE_ENABLED=true`

Not yet: onboarding, session-episode reforging, SSE streaming, multi-user auth,
outbound channels (email / WeChat / PWA).

## Docs

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — 结构定稿；末尾「施工纪律」是硬规矩
- [PLAN_companion.md](docs/PLAN_companion.md) — 产品定稿（全文在 cassette）
- [DESIGN_prompt_assembly.md](docs/DESIGN_prompt_assembly.md) — 拼装 / 引导 / 重铸对齐稿
- [MIN_MEMORY.md](docs/MIN_MEMORY.md) · [MIN_WAKE.md](docs/MIN_WAKE.md) — 记忆 / 主动触达

## License

MIT
