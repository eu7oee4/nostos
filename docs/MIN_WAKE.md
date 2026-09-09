# Min-wake

主动触达的最小站内版。命名一律 **wake**（不用 job）。

## 开关

`.env`：

```env
PROACTIVE_ENABLED=true
```

默认 `false`。关掉时不能预约新的 wake；进程仍会启动调度器，但不会武装 pending。

## 存哪

SQLite 表 `wakes`（与 `messages` 同库 `data/nostos.sqlite`）：

- `wake_at` / `note`（可选写死台词）/ `intent`（无 note 时到点再生成）
- `status`: `pending` | `fired` | `cancelled`

到点：往 `messages` 写入一条 `assistant`（站内「伙伴来找你」），**再推一条 Web Push
到用户手机**（见下面「推出去」）。站内那条是真相来源，推送是附加动作。

## 怎么测

```bash
# .env 打开 PROACTIVE_ENABLED=true 后
docker compose up --build

curl -s -X POST http://localhost:8787/wakes \
  -H 'Content-Type: application/json' \
  -d '{"delay_seconds":30,"note":"嘿，这是测试醒来。"}'

# 等 30 秒，刷新聊天页或：
curl -s http://localhost:8787/messages | head
curl -s http://localhost:8787/wakes
curl -s http://localhost:8787/health
```

也可在聊天里让伙伴 `wake_set`（需已开开关）。

## 说什么

- 带 `note`：原样投递那句写死的台词，不过模型
- 只带 `intent`：到点走**和聊天同一条**拼装管线（`app.context.assemble`），
  触发是 7b「没人找你，是你自己到点醒过来的」。所以他主动来找你时，profile /
  persona / 最近对话 / 记忆召回都在，和聊天里是同一个人。细节见
  [PROMPT_ASSEMBLY.md](./PROMPT_ASSEMBLY.md)。

## 推出去（Web Push）

只写进站内表的话，用户不打开网页就永远看不见——PLAN §1 的假设 2「它主动来」等于
没在测。所以 `fire_wake` 落库之后会调 `app.push.notify()`。

- **标准 Web Push + VAPID，服务器直发浏览器厂商的 endpoint**，不需要 Firebase 或任何
  第三方推送服务（PLAN §4.1：渠道也不该有平台中间人）
- VAPID 密钥对第一次用时**自动生成**到 `DATA_DIR/vapid_private.pem`（权限 600）。
  **换掉这个文件 = 换身份**，所有旧订阅立刻失效，它跟着 `data/` 一起备份
- `VAPID_SUBJECT` 要填**合法 mailto:**。`py_vapid` 只收 mailto（`https:` 直接抛），
  而 Apple 还校验域名——`mailto:…@localhost` 会被 403 `{"reason":"BadJwtToken"}` 拒掉
- 订阅存 SQLite 表 `push_subscriptions`，`endpoint` 唯一（重复订阅是覆盖不是新增）。
  推送返回 404/410 = 订阅过期，那一行**直接删掉**，留着只会每次都失败
- **推失败绝不影响 wake**：`notify()` 自己吞异常，只记 `nostos.push` 日志。站内那条
  assistant 已经落库、wake 已经 `fired`

### 硬前提：HTTPS + 装到主屏

Service Worker 要安全上下文，`http://局域网IP` 和 `http://tailscale-IP` 都不行。
自托管走 `tailscale serve`，见 [README-zh.md](../README-zh.md) 的 **1C**。

**iOS 只在 standalone PWA 模式下给 Web Push**——必须「添加到主屏幕」再从主屏图标
打开，Safari 标签页里 `pushManager.subscribe()` 拿不到东西。所以 #12 首次引导必须
把这一步做进去。

### 怎么测

```bash
# 手机上打开页面 → 添加到主屏 → 从主屏打开 → 点右上角「开启通知」
curl -s http://localhost:8787/health   # push_subscriptions 应该 >= 1

curl -s -X POST http://localhost:8787/wakes \
  -H 'Content-Type: application/json' \
  -d '{"delay_seconds":5,"note":"测试推送"}'
# 锁屏等那条通知；服务端日志看 nostos.push 那行 sent/failed/dropped
```

## 还没做

随机醒来策略（#10）、微信 / 邮件渠道（搁置，见 PLAN §4.1）。

⚠️ 已知问题：`_generate_wake_line` 生成的那句**开头几乎必带一段括号旁白**（实测 5/5），
推送之后顶到锁屏上，用户第一眼看到的是舞台说明。见 issue #16。
