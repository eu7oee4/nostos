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
- `status` 四态 + `reason`：
  - `pending` 等着开火
  - `fired` 开过了，`fired_at` 是真实开火时刻
  - `cancelled` **人**取消的：`wake_cancel`、`DELETE`、用户把随机关掉（reason 记开关名）
  - `skipped` **系统**没让它开，reason 必有：护栏挡了（`quiet_hours` / `daily_cap` /
    `min_interval` / `recent_chat`）、停机期间过点（`missed`）、已武装那条不再合规
    被重挑（`repick`）、`wake_at` 解析不了（`bad_wake_at`）

  cancelled 和 skipped 分开是 Raven 那条「失败降级要留痕」：以前两种都记 cancelled，
  事后分不清是人不要还是系统没给。`GET /stats` 的 `closed` 按 `status:reason` 分桶
- `source`: `manual`（模型 `wake_set` / `POST /wakes` 定的）| `auto`（随机醒来挑的，
  见 [RANDOM_WAKE.md](./RANDOM_WAKE.md)）

老库自动迁移：#10 之前的补 `source` 列；#10 之后、这版之前的 status CHECK 只认三态，
SQLite 改不了 CHECK，启动时**重建表**搬数据（`db._rebuild_wakes`），有测试盯着。

到点：标 `fired` + 往 `messages` 写入一条 `assistant`（站内「伙伴来找你」）是
**一个事务**（`db.fire_wake_tx`）——以前是两步两个连接，中间挂掉就会在重启时再开一次火，
用户收到两条一样的。然后**再推一条 Web Push 到用户手机**（见下面「推出去」）。站内那条
是真相来源，推送是附加动作。

## 并发：一次只跑一轮

同一个用户一把 `asyncio.Lock`（`app/locks.py`），`run_chat` 和 `fire_wake` 都整轮持锁。
挡的是两种交错：用户连发两条各自读到缺对方那条的历史；聊天正在工具循环里 wake 到点，
把一条 assistant 插进对话中间。wake 到点时正聊着就等那轮说完再开口。

⚠️ 锁不可重入，所以 `schedule_wake` 对**已到点**的（`delay_seconds=0` / 过去的时刻）
**不再 inline 开火**，改成挂进调度器下一拍跑，返回 `due_now: true`。模型在聊天里
`wake_set(delay_seconds=0)` 时聊天这轮正持着锁，inline 会死等自己。

## 重启

`restore_pending_wakes`：没到点的挂回去；过点但在 `MISFIRE_GRACE_SECONDS`（300）内的
挂到「现在」立刻开；**过点超过宽限的记 `skipped(missed)`，不补开**。以前是过点的全部
立刻开火——服务器停三天再起来，用户一口气收到停机期间攒的每一条推送，每条还要调一次模型。

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
  触发是 7b「轮到你说话。没有新消息」（措辞 09-10 改过，见 issue #16 和 PROMPT_ASSEMBLY）。所以他主动来找你时，profile /
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
- **安静时段里不推**：那条照样醒、照样生成、照样进聊天记录，只是不顶到锁屏上
  （静默投递）。护栏管的是「几点可以吵你」，不是「几点不许存在」——细节和为什么
  不是整条吞掉，见 [RANDOM_WAKE.md](./RANDOM_WAKE.md)

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

## 随机醒来

没人叫他、他自己挑个时候来找你，是这条主路径的**补充**——时刻仍由模型 tool 定，
随机只是补充，护栏只拒或改约。四条护栏（安静时段 / 最小间隔 / 日上限 / 刚聊过）存在
`data/prefs.json` 的 `wake{}`，和纠偏层同一个文件。

单独一份：[RANDOM_WAKE.md](./RANDOM_WAKE.md)。

## 看数：`GET /stats`

PLAN §11 候选指标里最便宜的那个先算出来：「它先开口」的接受率——最近 `days`（默认 7）
天开过火的 wake，有多少条在 `reply_hours`（默认 6）小时内等到了用户的下一句。按 `source`
分 manual / auto，另有 `closed` 按 `status:reason` 分桶看护栏在挡什么、停机漏了几条。
数据本来就在库里（`fired_at` + 下一条 user 的 `created_at`），以前只是没人算。

## 日志

每次 `fire_wake` 开一个轮 id（`[wake-xxxxxxxx]`，`app/trace.py`），这一轮里拼装、调模型、
落库、推送的日志全带同一个 id；聊天那边是 `[chat-xxxxxxxx]`。模型挂了用兜底句
「嘿，我来看你啦。」时**记 WARNING**，不静默。

## 还没做

微信 / 邮件渠道（搁置，见 PLAN §4.1）、护栏的设置页（现在只能 `PUT /prefs/wake`，
等 #12 一起做）、模型 `wake_set` 的数量硬顶或按 intent 覆盖（现在是纯累加，待定）。

⚠️ 已知问题：`_generate_wake_line` 生成的那句**开头几乎必带一段括号旁白**（实测 5/5），
推送之后顶到锁屏上，用户第一眼看到的是舞台说明。见 issue #16。
