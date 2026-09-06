# nostos

> 暂定名。AI 伙伴：记得你，也会来找你。自托管 / BYOK。

**English: [README.md](README.md)**

**伙伴，不是助手。** 当前阶段：**min-chat**（最小可聊）——能来回说话。还没有长期记忆、主动触达、nostools。

---

## 在电脑上使用（逐步）

### 0. 你需要有什么

- 一台 Mac / Linux（Windows 也行，命令略有差异）
- 已安装 [Git](https://git-scm.com/) 和 [Docker Desktop](https://www.docker.com/products/docker-desktop/)（推荐用 Docker；也可用本机 Python，见文末）
- 一个大模型 API Key（默认按 [DeepSeek](https://platform.deepseek.com/) 配置）

### 1. 克隆仓库并切到可聊分支

```bash
git clone https://github.com/eu7oee4/nostos.git
cd nostos
git fetch origin
git checkout feat/min-chat-loop
```

> 等 PR 合并进 `main` 之后，可以改成直接 `git checkout main` / `git pull`。

### 2. 配置环境变量

```bash
cp .env.example .env
```

用编辑器打开 `.env`，至少填：

```env
LLM_API_KEY=你的key
```

一般不用改的默认值：

- `LLM_BASE_URL=https://api.deepseek.com`
- `LLM_MODEL=deepseek-chat`
- `PORT=8787`
- `USER_ID=local`

保存文件。**不要**把 `.env` 提交到 git。

### 3. 启动服务

确认 Docker Desktop 已打开，然后：

```bash
docker compose up --build
```

第一次会拉镜像、装依赖，可能要一两分钟。看到 uvicorn / 容器在跑、没有立刻退出即可。

### 4. 在本机浏览器打开

地址必须是下面之一（不要用 `www.localhost.com`）：

- http://localhost:8787
- http://127.0.0.1:8787

健康检查：http://localhost:8787/health  
若 JSON 里 `has_key` 为 `false`，说明 `.env` 里的 key 没被读到——检查文件名是否真是 `.env`、是否在项目根目录、改完后是否重启过 compose。

### 5. 停掉服务

在跑 `docker compose up` 的终端按 `Ctrl+C`，或另开终端：

```bash
docker compose down
```

聊天记录在 `data/nostos.sqlite`，下次再 `up` 还在（别误删 `data/`）。

---

## 用手机聊

服务跑在**电脑**上。手机里的 `localhost` 指手机自己，所以电脑上的页面手机打不开——要用局域网或隧道。

**安全提醒：** 当前没有登录。谁能打开页面，谁就能聊、并消耗你的 API Key。链接不要发群；用完关掉隧道。

### 方法 1：同一 Wi‑Fi（最快，不出家门）

1. 电脑上保持 `docker compose up` 在跑，且本机 `http://localhost:8787` 能打开。
2. 查电脑的局域网 IP：
   - Mac 终端：`ipconfig getifaddr en0`（Wi‑Fi 常见是 `en0`；不行再试 `en1`）
   - 或「系统设置 → 网络 → Wi‑Fi → 详细信息」里看 IP
   - 形如 `192.168.1.23`
3. 手机连**同一 Wi‑Fi**（不要用「访客网络」；部分路由开了 AP 隔离会导致互通失败）。
4. 手机浏览器打开：`http://192.168.1.23:8787`（换成你的 IP）。
5. 若打不开：检查 Mac「防火墙」是否拦了 Docker；确认端口是 `8787`；确认 IP 没抄错。

### 方法 2：隧道（人不在同一网络也能聊）

电脑本地照常跑 nostos，再用隧道生成一个 **https** 链接给手机。

临时试用优先 **cloudflared**（步骤更少，可不注册）。需要账号面板、固定玩法时再用 ngrok。

#### 2A. cloudflared 临时隧道（推荐先试）

1. 安装（Mac）：
   ```bash
   brew install cloudflare/cloudflare/cloudflared
   ```
2. 确认 nostos 已在跑（`localhost:8787` 可打开）。
3. **另开一个终端**执行：
   ```bash
   cloudflared tunnel --url http://localhost:8787
   ```
4. 终端里会出现类似 `https://xxxx.trycloudflare.com` 的地址——整段复制。
5. 手机浏览器打开该 https 链接即可（不要求同一 Wi‑Fi）。
6. 用完在 cloudflared 那个终端按 `Ctrl+C`；链接立刻失效。

#### 2B. ngrok（备选）

适合：已经有 ngrok 账号、或更习惯它的 Dashboard / 文档生态。免费试用要注册，步骤比 cloudflared 临时隧道多一步。

1. 打开 https://ngrok.com/ 注册，在 Dashboard 复制 **Authtoken**。
2. 安装：`brew install ngrok/ngrok/ngrok`
3. 一次性配置：
   ```bash
   ngrok config add-authtoken <你的token>
   ```
4. nostos 在跑时，另开终端：
   ```bash
   ngrok http 8787
   ```
5. 复制 `Forwarding` 里的 `https://….ngrok-free.app`，手机打开。免费版可能有中间提示页，点继续即可。
6. 用完 `Ctrl+C`。

#### 不要做的事

- 不要把家里路由器把 `8787` 直接映射到公网（没有鉴权）。
- 不要把隧道链接发到公开群。

---

## 当前能做什么（min-chat）

- `GET /health`
- `GET /messages` — 当前 `USER_ID`（默认 `local`）的历史
- `POST /chat` `{"content":"..."}` — 写入用户句 → 调模型 → 写入回复
- SQLite：`data/nostos.sqlite`，时间戳由服务端盖

## 还没做

- 记忆 md / 召回、preferences、闹钟、nostools、SSE 流式、多用户鉴权

## 不用 Docker 时（可选）

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd ..
export PYTHONPATH=server DATA_DIR=./data
# 自行保证 LLM_API_KEY 等在环境里，或从项目根目录加载 .env
uvicorn app.main:app --app-dir server --reload --port 8787
```

## 文档

- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [PLAN_companion.md](docs/PLAN_companion.md)（全文暂链到 cassette）

## License

MIT
