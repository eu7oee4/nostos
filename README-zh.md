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

第一次会拉镜像、装依赖，可能要一两分钟。看到容器在跑、没有立刻退出即可。

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

## 手机怎么连

服务跑在**你的机器**上（笔记本或你自己的云主机）。手机里的 `localhost` 指手机自己。下面按场景选：

| | 场景 | 要不要暴露到公网 |
|---|---|---|
| **1A** | Tailscale：电脑一直开着，出门也能聊 | 否（虚拟局域网） |
| **1B** | 同一 Wi‑Fi，不出家门 | 否 |
| **2** | 临时公网隧道 | 是（有链接的人都能进） |
| **3** | **你自己**买 VPS / 云主机部署 nostos | 是（你自己的服务器） |

**安全：** 当前没有登录。谁打开页面，谁就能聊并烧你的 API Key。

### 1. 用「电脑的局域网 / 虚拟局域网 IP」访问

电脑上保持 `docker compose up`，本机先确认 `http://localhost:8787` 能开。然后在手机浏览器打开：

`http://<电脑的IP>:8787`

#### 1A. Tailscale（推荐：出门也能聊）

思路：手机和电脑都加入 Tailscale，相当于始终在一个虚拟局域网里；用电脑的 **Tailscale IP**（一般是 `100.x.x.x`）访问 `8787`。电脑要保持开机并跑着 nostos。

1. 电脑和手机都安装 [Tailscale](https://tailscale.com/)，用**同一个账号**登录并连上。
2. 在电脑上查看 Tailscale IP：  
   - Mac 菜单栏 Tailscale 图标，或终端：`tailscale ip -4`  
   - 形如 `100.86.12.34`
3. 手机浏览器打开：`http://100.86.12.34:8787`（换成你的 Tailscale IP）。  
   若开了 MagicDNS，也可用电脑的 Tailscale 主机名，同样加 `:8787`。
4. **手机缺点：** 很多环境下 **Tailscale 和「梯子」不能同时用**（都要抢 VPN 通道）。出门若必须挂梯子，手机上就会别扭。  
5. **Mac 可以同时用梯子：** Tailscale → **Settings**，关掉 **Use Tailscale DNS settings**（不要让 Tailscale 接管 DNS），再按你的习惯连系统代理 / 梯子。

#### 1B. 同一 Wi‑Fi（最简单，不出家门）

1. 手机和电脑连**同一 Wi‑Fi**（不要用访客网络；部分路由的 AP 隔离会导致互通失败）。
2. 查电脑局域网 IP：  
   - Mac：`ipconfig getifaddr en0`（不行再试 `en1`），或「系统设置 → 网络」  
   - 形如 `192.168.1.23`
3. 手机打开：`http://192.168.1.23:8787`。
4. 打不开时：查防火墙是否拦 8787、IP 是否抄错、是否真在同一网段。

### 2. 开一个临时公网隧道

适合：临时演示、或不想装 Tailscale。会得到一个 **https** 链接；**有链接约等于有钥匙**，用完关掉，别发群。

临时试用优先 **2A cloudflared**（步骤少，可不注册）。已有 ngrok 账号再用 **2B**。

#### 2A. cloudflared

```bash
brew install cloudflare/cloudflare/cloudflared
# nostos 已在 :8787 运行
cloudflared tunnel --url http://localhost:8787
```

把终端打印的 `https://….trycloudflare.com` 在手机打开。用完 `Ctrl+C`。

#### 2B. ngrok

```bash
brew install ngrok/ngrok/ngrok
ngrok config add-authtoken <token>   # 一次性，来自 https://ngrok.com/ dashboard
ngrok http 8787
```

复制 `Forwarding` 的 `https://…` 用手机打开。免费版可能有提示页。用完 `Ctrl+C`。

#### 不要做的事

不要把家里路由器把 `8787` 裸端口映射到公网（没有鉴权）。

### 3. 自己部署到云服务器（DIY）

这是**你自己**买一台 VPS / 云主机，把本仓库的 Docker 跑上去，再用手机浏览器访问——数据、域名、账单都在你这边。

**不是**项目方提供的「打开就用」托管站；那是另一条产品线，和这份开源 README 无关。

大致步骤（细节因云厂商而异，这里只给骨架）：

1. 买一台有公网 IP 的 Linux 云主机，放行安全组端口（至少 80/443；若直连 8787 也要放行）。
2. 安装 Docker，把本仓库 clone 上去，配置 `.env`（填你自己的 `LLM_API_KEY`）。
3. `docker compose up -d --build`，确认本机 `curl http://127.0.0.1:8787/health` 正常。
4. 强烈建议前面加 **Caddy / Nginx / Cloudflare** 做 HTTPS，不要长期裸奔 `http://公网IP:8787`。
5. 手机打开你的域名或 `https://你的域名`。

当前仓库**还没有**一键安装脚本 / 官方镜像文档；会按上面 compose 自行部署。等文档补全前，不熟 Linux 的用户更建议先用 **1A / 1B / 2**。

同样没有登录：公网能打开 = 别人也能聊、烧你的 key。上公网前至少要自己加一层门禁（反向代理 Basic Auth、Tailscale 只听内网、或以后仓库提供的 token），不要裸奔。

---

## 当前能做什么（min-chat）

- `GET /health`
- `GET /messages` — 当前 `USER_ID`（默认 `local`）的历史
- `POST /chat` `{"content":"..."}` — 写入用户句 → 调模型 → 写入回复
- SQLite：`data/nostos.sqlite`，时间戳由服务端盖

## 还没做

- 记忆 md / 召回、preferences、闹钟、nostools、SSE 流式、多用户鉴权、DIY 云部署详细教程

## 不用 Docker 时（可选）

```bash
cd server
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cd ..
export PYTHONPATH=server DATA_DIR=./data
uvicorn app.main:app --app-dir server --reload --port 8787
```

## 文档

- [ARCHITECTURE.md](docs/ARCHITECTURE.md)
- [PLAN_companion.md](docs/PLAN_companion.md)（全文暂链到 cassette）

## License

MIT
