# 会话段与重铸

PLAN §4.3 拍的「不做滑动窗口，做会话段」，Notion「nostos 记忆系统设计对照」第 ⑨ 点
2026-09-14 定稿的生命周期，落地顺序第 2 步。代码在 `server/app/segments.py`，
表在 `db.py` 的 `segments` / `episodes`。

## 解决什么

原来 `list_recent_turns(limit=40)`：硬编码最近 40 行，每轮前缀都在变（最老那行掉出去），
缓存吃不到；对话滚出窗口就断了连续性，没提炼、没痕迹（issue #11）。

现在：**段内纯追加吃缓存，重铸时靠 episode 接上。**

## 三个名词

- **段（segment）**：拼装给模型看的那截历史。表里一行：`tail_from_msg_id`（历史从这条起，
  含）、`episode_text`（段开头那块回忆，**段内冻结**）、`reason`（`init` / `hard` / `dead_cache`）。
  当前段 = 该用户最新一行。
- **episode**：角色自己写的一段回忆，**原模型、原上下文**提炼出来的。三栏：主题 / 摘要 / 正文。
  存 `episodes` 表 + 一份 `data/episodes/<user>/seg-<段id>.md`（角色日记，用户可看不可改，
  同一段重复提炼按段 id 覆盖）。**不进 memories、不做向量、不进召回**。记忆条目只从对话中的
  `memory_write_item` / `_feel` 来（Notion ⑫），提炼不产条目。
- **重铸（recast）**：开新段。**只有一种形状**：新段 = 固定前缀 + 上一份 episode + 最近 N 轮
  原文。episode 和那 N 轮会重叠，重叠是故意的：episode 给连续性，原文给具体语境和兜底。

「轮」的口径：一行 done 的 assistant = 一轮。wake 也落一行 assistant，所以 wake 那一轮算数，
它会让 episode 旧一轮。

## 三条规则

1. 重铸只在两个时刻发生：**硬闸轮末**；或者**用户回来时缓存已死**。
2. 重铸前 episode 必须**新鲜**：距当前 ≤ N 轮。不新鲜就先提炼。这个 N 和尾巴的 N 是同一个数
   （`SEGMENT_TAIL_TURNS`），保证尾巴盖住 episode 之后的全部对话，要改一起改。
3. 闲置时只做一件事：段过了软线且 episode 不新鲜就提炼，否则不动。段不关，episode 只存着。

```
段内纯追加
├─ 硬闸轮末            → 提炼（缓存读）→ 重铸                     segments.after_turn
├─ 闲置超阈值 且 过软线 → episode 新鲜？ 是：不动 / 否：提炼，段不关     segments.idle_check
└─ 用户回来                                                        segments.before_turn
     ├─ 距上次调用 < TTL → 续原段，存着的 episode 先放着
     └─ 距上次调用 ≥ TTL → 有新鲜的存着的 episode：重铸 / 没有：续段，付一次全价
```

「没有新鲜的就续段付全价」只会是没过软线的短段（过了软线闲置时就提炼了），前缀小，不用补救。
内容离开上下文只有重铸一条路，重铸前必有提炼，所以不会漏。

## 提炼长什么样

就是 wake 那条管线：前缀（system / profile / persona / prefs / episode 块 / 本段历史）
逐字节和聊天一样，尾巴挂一条〔提炼〕触发句（`assemble.DISTILL_LINE`），`tools=None`，不带召回块。
所以它是**缓存读**：硬闸时前缀本来就是热的，闲置时闲置阈值小于 TTL。

触发句强制三栏、限长、「把上面那份回忆也并进来」（否则第三段就把第一段忘了）。角色只提供口吻，
格式不交给他自由发挥。**触发句和产物都不进 messages 表**，用户在对话流里看不见。

⚠️ 09-14 真模型冒烟踩到的坑：第一版触发句只说「写成你自己的回忆，第一人称」，deepseek-chat
把「我」写成了**用户**（「今天下午跟 nostos 说最近有点累，它问…」），替对方写了日记。
改成「「我」是你，是回话的这一方；对方写她 / 他，不要替对方写日记」之后正常。改这句先跑一遍
`eval/smoke_distill.py`，视角反了比格式散了更难在日志里看出来。

产物落 `episodes` 表：`status`（`stored` 存着 / `used` 用于重铸 / `superseded` 没用上就被下一次
提炼覆盖）、`trigger`（`idle` / `hard`）、`covers_to_msg_id`（提炼时看到的最后一条，新不新鲜
从它数）、token 三列 + ms。超 `EPISODE_MAX_CHARS` 兜底截断并记 WARNING。

## 参数（`.env`）

| 名字 | 默认 | 含义 |
|---|---|---|
| `SEGMENT_SOFT_CHARS` | 6000 | 软线：闲置时值不值得提炼 |
| `SEGMENT_HARD_CHARS` | 20000 | 硬线：轮末必重铸 |
| `SEGMENT_TAIL_TURNS` | 10 | 重铸保留的最近轮数 = episode 新鲜的最大轮数 |
| `SEGMENT_IDLE_MINUTES` | 15 | 闲置阈值。**必须小于 TTL**，闲置提炼才是缓存读 |
| `SEGMENT_CACHE_TTL_MINUTES` | 20 | 距上一次任何 LLM 调用多久算缓存已死。Claude 侧 60 |
| `EPISODE_MAX_CHARS` | 800 | 滚动块长度上限 |

软线 / 硬线按**字符**算（段内 user + assistant 正文长度和），不是 token：没有分词器，字符是
确定的、可测的代理。中文在 DeepSeek 上大约 0.6 token / 字。每次提炼的日志带真实 `prompt_tokens`，
拿它校准。

「缓存死了没有」的起点是**上一次任何 LLM 调用**（`db.last_llm_at`：最新一行 assistant 和最新
一份 episode 取晚的那个），不是上一条用户消息。wake 开火和提炼本身都会刷新。DeepSeek 拿不到
官方 TTL，按闲置阈值略大估；用提炼那次的 `cache_hit_tokens` 校准——接近 `prompt_tokens` 才说明
「提炼永远是缓存读」成立，连续不命中就是闲置阈值定大了或者 DeepSeek 的缓存比想的短。

## 施工细节

- **锁**：`segments.*` 所有入口假定调用方已持 turn 锁。硬闸在 `chat_loop._commit` 里、回复落库
  之后、返回之前跑（锁内）：提炼那几秒用户又发了消息，下一轮等它，可以接受。炸了只记 WARNING，
  回复已经落库不受影响。
- **闲置检查**是 APScheduler 一条 date 任务（`scheduler.arm_idle_check`，id `idle-<user>`，
  `replace_existing`），每次聊天 / wake 之后重挂到「现在 + 闲置阈值」。到点拿锁跑 `idle_check`；
  回 `not_idle`（中间又有调用）就按上一次调用重挂。它不是 wake：不开口、不落消息。
- **提炼失败不重铸**：没有新鲜 episode 就重铸等于把这段忘掉。段越过硬线继续长，下一轮末再试，
  WARNING 留痕。`list_segment_turns` 有 2000 行保险丝。
- **老库升级**：第一次 `ensure_segment` 时 tail 取最近 40 行，升级那一刻模型眼里的历史和原来一样。
- 保留的 N 轮原文时间戳保持冻结（PLAN §6.3），不重新渲染——它们就是同一批行。
- 后端 wake 也从段拿历史（`segments.context`），前缀和聊天逐字节相同才吃同一份缓存。
  wake 不做「回来时重铸」那步：它是系统到点来的，不是用户回来。

## 观测

- 每次提炼一行 `nostos.segment` INFO：`episode distilled id= segment= trigger= covers_to= turns=
  chars= prompt_tokens= cache_hit_tokens= completion_tokens= ms=`
- 每次重铸一行：`segment recast reason= old= new= tail_from= episode=`
- 每次闲置检查一行 `nostos.wake`：`idle check user= -> not_idle|under_soft|fresh|distilled|distill_failed`
- 轮末汇总多了 `segment=<id>`
- `GET /stats` 的 `segments`：当前段多少轮 / 多少字、最新 episode 新不新鲜、**利用率**
  （`distilled` / `used` / `superseded`）。`superseded` 占比高 = 「闲置且过软线就提炼」在白烧，
  那时抬高软线或放宽 N。被覆盖的 episode 白费的只是那次调用，日记按段 id 覆盖不会留重复
- `GET /episodes`、`GET /episodes/{id}`：角色日记。没有 DELETE / PUT

## 验收（issue #11）

`nostos.llm` 那行 usage 的 cache 字段：段内应该稳定命中；重铸那一次全 miss 是预期的，之后回稳态。
连续为 0 = 有隐形失效源。

## 不做的

- 不用 LLM 判断会话边界，闲置关段就是边界
- 不设「第二次闲置」定时器，用户回来那一刻算一下就行
- 提炼不用「更好的模型」：换模型就换了口味和上下文，回忆得是角色自己写的
