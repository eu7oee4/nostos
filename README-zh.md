# nostos

> 暂定名。AI 伙伴：记得你，也会来找你。自托管 / BYOK。

**English: [README.md](README.md)**

**伙伴，不是助手。** 当前阶段：**min-chat**（最小可聊）——能来回说话。还没有长期记忆、主动触达、nostools。

## 快速开始

```bash
git clone https://github.com/eu7oee4/nostos.git
cd nostos
git checkout feat/min-chat-loop   # 合并进 main 前先切这个分支
cp .env.example .env   # 填写 LLM_API_KEY（默认 DeepSeek）
docker compose up --build
```

在**这台电脑**上打开 **http://localhost:8787**（不要用 `www.localhost.com`）。健康检查：http://localhost:8787/health

不用 Docker：

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd ..
export PYTHONPATH=server DATA_DIR=./data
# 自行加载 .env 或 export LLM_API_KEY
uvicorn app.main:app --app-dir server --reload --port 8787
```

## 用手机聊

服务跑在电脑上。手机里的 `localhost` 指的是手机自己，所以要换下面两种方式之一。

### 1. 同一 Wi‑Fi（最快试）

1. 电脑上保持 `docker compose up` 在跑。
2. 查电脑的局域网 IP（Mac 示例：`ipconfig getifaddr en0`，或「系统设置 → 网络」）。
3. 手机浏览器打开 `http://<局域网IP>:8787`（例如 `http://192.168.1.23:8787`）。

注意：手机和电脑要在同一 Wi‑Fi（不要用访客网络 / AP 隔离）。连不上就查 Mac 防火墙是否放行 8787。**只适合信任的网络**——现在还没有登录鉴权。

### 2. 隧道（不在同一网络也能聊）

电脑本地照常跑服务，再用隧道露出一个 HTTPS 链接，手机打开即可：

- [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-apps/)（`cloudflared tunnel ...`）
- [ngrok](https://ngrok.com/)（`ngrok http 8787`）
- 若已在用 Tailscale：[Tailscale Funnel](https://tailscale.com/kb/1223/funnel)

公网链接相当于钥匙，别随手发群。min-chat 仍然没有鉴权。

**不要**在没有防护的情况下把 8787 直接端口映射到公网。

## 当前能做什么（min-chat）

- `GET /health`
- `GET /messages` — 当前 `USER_ID`（默认 `local`）的历史
- `POST /chat` `{"content":"..."}` — 写入用户句 → 调模型 → 写入回复
- SQLite：`data/nostos.sqlite`，时间戳由服务端盖

## 还没做

- 记忆 md / 召回、preferences、闹钟、nostools、SSE 流式、多用户鉴权

## 文档

- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [PLAN_companion.md](docs/PLAN_companion.md)（全文暂链到 cassette）

## License

MIT
