# Random wake（随机醒来）

命名一律 **wake**，不叫 job。这份讲的是 wake 的**补充路径**：没人叫他、他自己挑个
时候来找你。主路径（模型用 `wake_set` 定时刻）见 [MIN_WAKE.md](./MIN_WAKE.md)。

## 一句话

**时刻仍然由模型定，随机只是补充；护栏只拒或改约，不当决策者。**

DESIGN §9 步骤 5 原来写的是「去掉脚本随机 next_auto 主路径」。issue #10 是明确改判：
随机醒来要做，但做成**护栏 + 补充路径**——不是把决策权交回脚本。所以有两条硬规矩：

1. **护栏只是护栏。** 安静时段 / 日上限 / 最小间隔 / 刚聊过，四条只管随机这条路径挑
   不挑得出点、到点还开不开火。模型 `wake_set` 定的那条照定、照到点执行
2. **wake 开火保持 `tools=None`。** PLAN §13 抄 Raven 的那条：主动消息不重新进入带
   工具的 agent loop，用架构堵死「对一条提醒采取行动」。别为了让他「随机醒来时看看
   有没有事做」把工具放回去——那正是这条要堵的

## manual 和 auto

`wakes` 表加了一列 `source`：

| source | 谁定的 | 护栏 | 推送 |
|---|---|---|---|
| `manual` | 模型 `wake_set` / `POST /wakes` | **不受约束**，到点就执行 | 安静时段内**静默投递**（见下） |
| `auto` | `ensure_auto_wake` 掷骰子挑的 | 四条全查，武装时查一次、到点再查一次 | 照推（它压根不会落进安静时段） |

老库升上来会自动 `ALTER TABLE` 补这一列，已有的行一律算 `manual`。

### 安静时段里的 manual：静默投递

半夜到点的那条 manual wake，**照常醒、照常调模型生成、照常落进聊天记录，只是不发
Web Push**。

护栏管的是「几点可以吵你」，不是「几点不许存在」。第二天早上打开就看见他半夜说过什
么——这是伙伴该有的样子；凌晨三点震你一下不是。而把那条整个吞掉更糟：他说过的话没了，
「记得」这件事就有洞。

站内那条 assistant 始终是真相来源，推送只是附加动作——这条口径和 #13 一样，见
MIN_WAKE「推出去」。

## 护栏存哪

`data/prefs.json` 的 `wake{}` 段，和纠偏层的 `style[]` **同一个文件**（`app/prefs.py`）。
`feat/random-wake` 分支上那份 `schedule/prefs.py` + `wake_prefs.json` 不要合过来——
一件事两个存储层，迟早对不上。

没设置过就跑默认值，**不写盘**：

| 键 | 默认 | 管什么 |
|---|---|---|
| `quiet_hours` | 开，`23:00-08:00` | 这段时间不随机醒；manual 落这段只是不推送。可以写多个窗口，跨夜（start > end）合法 |
| `min_interval` | 开，`240` 分钟 | 两次**随机**醒来至少隔多久（30 分钟 ~ 24 小时） |
| `daily_cap` | 开，`3` | 每个**本地日**最多几次随机醒来（1 ~ 20） |
| `recent_chat` | 开，`45` 分钟 | 用户刚说过话就别随机来（正聊着还主动出现是发癫） |
| `random` | 开，horizon `18` 小时 | 随机醒来总开关 + 往后最多摸多远挑那个点 |

三条计数类的护栏（最小间隔 / 日上限 / 刚聊过里的前两条）**只数 `source='auto'` 的
开火**：你让他九点叫你起床，不该吃掉他自己来找你的额度。

时区用 `.env` 的 `TIMEZONE`（和消息戳同一个），总开关仍是 `.env` 的
`PROACTIVE_ENABLED`。都不在 prefs 里再开一份——同一件事两个真相来源是 bug 温床。

**模型没有这段的入口。** 它能写 `style[]`（`prefs_write`），但护栏是用户的东西，
不是他能自己放宽的。`wake_list` 也只列 manual：随机那条他管不了，列出来只会诱他去
`wake_cancel`，然后下一轮又被补回来——工具回 ok 却什么都没变，最难查。

## 调度

```
ensure_auto_wake(uid)
  ├ 随机关掉 → 把待开火的 auto 全撤掉，收工
  ├ 已经武装了一条、且还过得了护栏 → 原样留着（幂等，不重摇）
  └ 没有 / 已失效 → next_auto_wake_at 挑一个点 → schedule_wake(source="auto")
```

调用点：进程启动、每轮聊天之后、随机醒来开火或被挡之后、`PUT /prefs/wake` 之后。

**「还合规就留着」这条很重要**：否则每来一条消息、每次重启都重挑一次，等于「摇到不
喜欢的点就再摇一次」，随机也就不随机了，还会把 `wakes` 表刷成一长串 `skipped(repick)`。
每轮聊天之后仍要调，是因为用户刚说的那句话可能让已武装那条撞进 `recent_chat` 冷却里。

到点复查（`can_fire_now`）挡下来的那条**一个 token 都不烧**——记 `skipped` + 挡它的
那条护栏名（`quiet_hours` / `daily_cap` / …）、再重挑，根本不调模型。烧钱的是生成
那句话，不是查四个条件。`GET /stats` 的 `closed` 能看到每条护栏各挡了几次。

### 挑点为什么是「重摇」不是「挪出安静时段」

分支原版把落进安静时段的点往后挪到窗口边缘。默认 23:00-08:00 占掉 18 小时窗口的一
半，于是**一多半的随机点会堆在 08:00 那几分钟上**——那不叫随机醒来，那叫每天早八准时
打卡，活人感正好死在这里。

现在是拒绝重采样：撞上就重摇，最多 40 次，全撞上才退回「最早允许的那一刻」。一半时间
可用时 40 次全撞的概率是 9e-13。

实测（本地 15:00 起摇 500 次，默认 23:00-08:00 + 18 小时 horizon）：

| | 最挤的那一小时 |
|---|---|
| 挪到窗口边缘（分支原版） | `08:00` 吃掉 **292 / 500** |
| 拒绝重采样（现在） | 最多的一小时 **62 / 500**，摊在 9 个可用小时上 |

## API

```bash
curl -s localhost:8787/prefs/wake                    # 看护栏
curl -s -X PUT localhost:8787/prefs/wake \
  -H 'Content-Type: application/json' \
  -d '{"daily_cap":{"max":1}}'                       # 合并写入：其他几条不动
curl -s localhost:8787/health                        # random_wake: enabled / next_at / guardrails
curl -s 'localhost:8787/wakes?status=pending'        # 每行带 source
```

`PUT` 是**合并**不是整体替换（设置页按单个开关提交时唯一不会误伤的语义），存完立刻
重算下一次随机醒来——不然改完得等下一次聊天或重启才生效，而「我把安静时段调宽了他今
晚还是没来」这种是查不出来的。写坏了不会炸：格式不对的安静时段整条丢掉并在
`nostos.prefs` 留 WARNING，数值越界夹回范围内。

## 怎么测

```bash
# .env: PROACTIVE_ENABLED=true
docker compose up --build

curl -s localhost:8787/health | jq .random_wake      # next_at 应该有值
# 想马上看到效果：把窗口调窄、间隔调到最小，再看 next_at 变没变
curl -s -X PUT localhost:8787/prefs/wake -H 'Content-Type: application/json' \
  -d '{"min_interval":{"minutes":30},"random":{"max_horizon_hours":1}}' | jq .auto_wake

# 静默投递：把此刻圈进安静时段，再下一条 manual
curl -s -X PUT localhost:8787/prefs/wake -H 'Content-Type: application/json' \
  -d '{"quiet_hours":{"enabled":true,"windows":[{"start":"00:00","end":"23:59"}]}}'
curl -s -X POST localhost:8787/wakes -H 'Content-Type: application/json' \
  -d '{"delay_seconds":5,"note":"安静时段测试"}'
# 预期：/messages 里有这条；手机锁屏没动静；日志 "in quiet hours: delivered in-app, no push"
```

`next_at` 一直是空 = 现在没武装，看 `nostos.wake` 日志里 `no_slot` / `not armed`
那行，多半是护栏把窗口掐没了（比如安静时段写满了一整天）。

## 还没做

- **设置页**。现在只能 `PUT /prefs/wake`，菜单里没有开关（分支上那个
  `web/settings.html` 没搬过来，等 #12 一起做）
- `policy_patrol_minutes`（定时巡逻兜底）没搬。主路径是 date 武装下一条，巡逻是
  为「进程长期不重启、prefs 被手改」准备的兜底，现在用不上
- issue #16 那个括号旁白仍在：随机醒来生成的那句同样会中招
