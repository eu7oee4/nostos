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

到点：往 `messages` 写入一条 `assistant`（站内「伙伴来找你」）。

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

## 还没做

随机醒来策略、站外渠道（微信 / 邮件 / PWA）。
