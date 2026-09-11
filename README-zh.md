# nostos

> 暂定名。AI 伙伴：记得你，也会来找你。自托管 / BYOK。

**English: [README.md](README.md)**

**伙伴，不是助手。** 当前阶段：**min-wake**——能来回说话、把长期记忆写成 markdown 文件、到点自己来找你。

---

## 在电脑上使用（逐步）

### 0. 你需要有什么

- 一台 Mac / Linux（Windows 也行，命令略有差异）
- 已安装 [Git](https://git-scm.com/) 和 [Docker Desktop](https://www.docker.com/products/docker-desktop/)（推荐用 Docker；也可用本机 Python，见文末）
- 一个大模型 API Key（默认按 [DeepSeek](https://platform.deepseek.com/) 配置）

### 1. 克隆仓库

```bash
git clone https://github.com/eu7oee4/nostos.git
cd nostos
```

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

| | 场景 | 收得到主动通知 | 要不要暴露到公网 |
|---|---|---|---|
| **1A** | Tailscale IP：出门也能聊 | ❌ | 否（虚拟局域网） |
| **1B** | 同一 Wi‑Fi，不出家门 | ❌ | 否 |
| **1C** | **Tailscale serve（HTTPS）** | ✅ | 否（仅 tailnet 可达） |
| **2** | 临时公网隧道 | ✅ | 是（有链接的人都能进） |
| **3** | **你自己**买 VPS / 云主机部署 nostos | ✅ | 是（你自己的服务器） |

> ### ⚠️ 想收「它主动来找你」的通知，必须 HTTPS
>
> nostos 的主动触达走 **Web Push**（PWA 推送）。浏览器规定 Service Worker 只在
> **安全上下文**里能注册——`http://192.168.x.x:8787` 和 `http://100.x.x.x:8787`
> （Tailscale IP）**都不算**，SW 根本装不上，也就拿不到订阅。
>
> 所以 **1A / 1B 这两条只能用来聊天，收不到主动消息**。要那半个产品，用 **1C**。
>
> **iOS 还有一条**：必须用 Safari 打开、「添加到主屏幕」、再**从主屏图标打开**。
> Safari 标签页里 iOS 不给 Web Push，页面上那个「开启通知」按钮会是灰的。

**安全：** 没有账号系统，只有一道门：`.env` 里的 `ACCESS_TOKEN`。**留空 = 没门**，
谁打开页面谁就能聊并烧你的 API Key——1A / 1B / 1C 在自己的局域网 / tailnet 里可以这样；
**2 和 3 走公网，必须填**一个长随机串（`openssl rand -hex 24`）。填了之后：

- 手机第一次打开 `https://…/?token=<那个串>`，服务端种 cookie，之后这台设备不用再带
- curl / 脚本：`-H 'Authorization: Bearer <那个串>'`
- 没带或带错：全部接口 401（`/health` 也在门内；PWA 的 `sw.js` / `manifest.json` / 图标不在）

### 1. 用「电脑的局域网 / 虚拟局域网 IP」访问

电脑上保持 `docker compose up`，本机先确认 `http://localhost:8787` 能开。然后在手机浏览器打开：

`http://<电脑的IP>:8787`

#### 1A. Tailscale IP（能聊，但收不到通知）

> 这条走的是 `http://100.x.x.x:8787`，**不是安全上下文，收不到主动通知**。
> 想要通知看 [1C](#1c-tailscale-serve推荐拿真证书还能收通知)。

思路：手机和电脑都加入 Tailscale，相当于始终在一个虚拟局域网里；用电脑的 **Tailscale IP**（一般是 `100.x.x.x`）访问 `8787`。电脑要保持开机并跑着 nostos。

1. 电脑和手机都安装 [Tailscale](https://tailscale.com/)，用**同一个账号**登录并连上。
2. 在电脑上查看 Tailscale IP：  
   - Mac 菜单栏 Tailscale 图标，或终端：`tailscale ip -4`  
   - 形如 `100.86.12.34`
3. 手机浏览器打开：`http://100.86.12.34:8787`（换成你的 Tailscale IP）。  
   若开了 MagicDNS，也可用电脑的 Tailscale 主机名，同样加 `:8787`。
4. **手机缺点：** 很多环境下 **Tailscale 和「梯子」不能同时用**（都要抢 VPN 通道）。出门若必须挂梯子，手机上就会别扭。  
5. **Mac 可以同时用梯子：** Tailscale → **Settings**，关掉 **Use Tailscale DNS settings**（不要让 Tailscale 接管 DNS），再按你的习惯连系统代理 / 梯子。

#### 1B. 同一 Wi‑Fi（最简单，同样收不到通知）

1. 手机和电脑连**同一 Wi‑Fi**（不要用访客网络；部分路由的 AP 隔离会导致互通失败）。
2. 查电脑局域网 IP：  
   - Mac：`ipconfig getifaddr en0`（不行再试 `en1`），或「系统设置 → 网络」  
   - 形如 `192.168.1.23`
3. 手机打开：`http://192.168.1.23:8787`。
4. 打不开时：查防火墙是否拦 8787、IP 是否抄错、是否真在同一网段。

#### 1C. Tailscale serve（推荐：拿真证书，还能收通知）

`tailscale serve` 把本机端口挂到 `https://<机器名>.<tailnet>.ts.net`，Tailscale 自动
签**真证书**，而且**只有你自己 tailnet 里的设备够得着**，不裸奔公网。这是目前唯一
一条「不暴露公网 + 能收主动通知」的路。

```bash
# nostos 已在 :8787 跑着
tailscale serve --bg http://127.0.0.1:8787
# → https://<机器名>.<tailnet>.ts.net
```

**后台要先开三样，少一个都不行**（在 https://login.tailscale.com/admin/dns ）：

1. **MagicDNS** —— 证书签的就是 MagicDNS 名字
2. **HTTPS Certificates**
3. **Serve** —— 没开的话 `tailscale serve` 会直接报
   `Serve is not enabled on your tailnet` 并给你一个开通链接

⚠️ 只开了 Serve 的症状很误导：`tailscale serve status` 显示一切正常、代理也接上了，
但 443 上握不了手（`SSL_ERROR_SYSCALL`），`tailscale cert <名字>` 报
`your Tailscale account does not support getting TLS certs`。那是缺 2 或 3。

⚠️ **开发机上挂梯子会劫持 `*.ts.net` 的解析**：Clash / Surge 那类 TUN 模式的
fake-ip（`198.18.0.0/15`）会把名字解析成假 IP，本机自己访问就连不上。手机上一般
没这问题（本来也不需要挂梯子访问 tailnet）。真要在开发机上自测，规则里加：

```
DOMAIN-SUFFIX,ts.net,DIRECT
IP-CIDR,100.64.0.0/10,DIRECT
```

关掉：`tailscale serve --https=443 off`

### 2. 开一个临时公网隧道

适合：临时演示、或不想装 Tailscale。会得到一个 **https** 链接；**先填 `ACCESS_TOKEN`**（见上面「安全」），不然有链接约等于有钥匙。用完关掉，别发群。

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

公网能打开 = 别人也能聊、烧你的 key。上公网前**必须填 `ACCESS_TOKEN`**（见上面「安全」）；反向代理再加一层 Basic Auth 也不冲突。不要裸奔。

---

## 当前能做什么（min-wake）

**聊天**

- `GET /health` — 含 `has_key` / `memory_count` / `pending_wakes` / `random_wake`
- `GET /stats` — 「它先开口」的接受率（最近 7 天开火的 wake 有多少条 6 小时内等到回话）、
  被护栏挡掉 / 停机漏掉的按原因分桶
- `GET /messages` — 当前 `USER_ID`（默认 `local`）的历史
- `POST /chat` `{"content":"..."}` — 写入用户句 → 调模型 → 写入回复
- SQLite：`data/nostos.sqlite`（WAL），时间戳由服务端盖；同一用户一次只跑一轮
- 日志每行带轮 id `[chat-…]` / `[wake-…]`，一轮的拼装、调模型、工具、落库 grep 一个 id 全在

**长期记忆**（详见 [MIN_MEMORY.md](docs/MIN_MEMORY.md)）

- 落在 `data/memories/<USER_ID>/*.md`，人能读、能改、能导出
- 模型自己用 `memory_write` / `memory_read` / `memory_list` 读写
- `GET /memories`、`GET /memories/{name}` 可以直接翻

**主动触达**（详见 [MIN_WAKE.md](docs/MIN_WAKE.md)）

- `POST /wakes` `{"delay_seconds":30}` 预约一次「到点来找你」
- `GET /wakes` 看待办的（每行带 `source`；`?status=` 看全部，`skipped` 行带 `reason`）；
  聊天里也能让他自己 `wake_set` / `wake_cancel`
- 重启时过点超过 5 分钟的不补发，记 `skipped(missed)`——停机三天不会一口气推三天的
- **默认关**：`.env` 里 `PROACTIVE_ENABLED=true` 才生效
- 到点那条会推到手机上（Web Push / PWA，要 HTTPS + 加到主屏，见上面 **1C**）

**随机醒来**（详见 [RANDOM_WAKE.md](docs/RANDOM_WAKE.md)）

- 开着 `PROACTIVE_ENABLED` 时，除了他自己预约的，还会随机挑时候来找你
- 四条护栏：安静时段（默认 `23:00-08:00`）/ 最小间隔 4 小时 / 每天最多 3 次 /
  刚聊过 45 分钟内不来
- `GET`、`PUT /prefs/wake` 调护栏（合并写入，改完立刻生效）；不想要就
  `{"random":{"enabled":false}}`
- 安静时段里他自己预约的那条**照样来，但不推送**——第二天打开就看见，不半夜震你

## 还没做

- 首次进入的引导（现在直接就是空聊天框）
- 会话段重铸（长对话的 token 上限）
- 微信 / 邮件渠道（站外现在只有 Web Push / PWA，见 [MIN_WAKE.md](docs/MIN_WAKE.md)）
- SSE 流式、多用户（现在只有单个 `ACCESS_TOKEN`）、DIY 云部署详细教程
- 备份（含真还原演习）

## 跑测试

```bash
pip install -r server/requirements-dev.txt
pytest            # 或 make test；配置在根目录 pytest.ini
```

测试自己起临时 `DATA_DIR`，不碰 `data/`，不需要 API key。CI 每次 push 都跑。

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

- [ARCHITECTURE.md](docs/ARCHITECTURE.md) — 结构定稿；末尾「施工纪律」是硬规矩
- [PLAN_companion.md](docs/PLAN_companion.md) — 产品定稿全文（MVP 要证伪什么、不做清单、安全合规）
- [DESIGN_prompt_assembly.md](docs/DESIGN_prompt_assembly.md) — 拼装 / 首次引导 / 会话段重铸的对齐稿
- [PROMPT_ASSEMBLY.md](docs/PROMPT_ASSEMBLY.md) — 一次调模型到底注入什么，和为什么长这样
- [MIN_MEMORY.md](docs/MIN_MEMORY.md) — 记忆存哪、怎么进对话
- [MIN_WAKE.md](docs/MIN_WAKE.md) — 主动触达怎么开、怎么测

## License

MIT
