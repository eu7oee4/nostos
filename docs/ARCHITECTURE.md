# nostos 架构定稿（暂定名）

> 仓名 `nostos`、触角层目录 `nostools` 均为**暂定**，可改。本文锁的是结构，不是商标。

## 产品面

- **开源默认**：自托管单用户，BYOK；`docker compose up` + `LLM_API_KEY`
- **托管网页**（你们自己）：同一代码 + 私密配置；多用户/token **不在本公开仓实现**
- **定位**：伙伴，不是助手。MVP 证伪「记得 + 主动来」；记忆可看可导出
- **角色**：每用户一个伙伴
- **触角**（`nostools/`）：活人感 / 对外感官，**不是**生产力 plugin 商店。MVP **零可选触角**

## 运行时

```
[web] --SSE(后续)--> [FastAPI] --HTTPS--> [Messages API]
                        │
                        ├── SQLite（一律带 user_id；本机默认 local）
                        ├── data/memories/*.md + index.json
                        └── APScheduler（主动触达；默认关）
```

- 直调 HTTP，不上 agent SDK/CLI
- 不要 Redis / Postgres / 向量库服务
- 默认模型：DeepSeek（可换；换模型须显式）

## Day1 触角地基

- 统一 **tool_use** 注册表（`server/app/nostools/`）
- 内置：记忆读写、闹钟（产品本身，可默认开）
- 其余能力默认关；出站副作用：人批 + 留痕（stub：`_run_tool` 见到 `outbound`
  直接拒绝并记 WARNING，批准通道接上之前没有一条能跑）
- 凭证按 `user_id` 隔离，不进 prompt
- 不建商店 / 热加载 / browser sidecar

## 施工纪律

- 第一天就有 `user_id`，无「可变当前用户」全局
- **同一用户一次只跑一轮**：`run_chat` 和 `fire_wake` 共用一把按 `user_id` 的锁
  （`app/locks.py`）。锁不可重入——持锁期间不许再进任何会拿这把锁的路径
- 时间戳服务端盖、不进 system；召回挂当轮尾
- 动作走 tool_use，不走正文标记；**唯一执行入口**是 `chat_loop._run_tool`，
  `side_effect` 在那里生效（`outbound` 无批准通道一律拒 + WARNING；`write` 留痕）
- 有代价的动作**原子落库**：wake 标 fired + 写消息一个事务（`db.fire_wake_tx`）
- 降级要留痕：wake 的 `skipped` 带 reason、兜底句记 WARNING，不静默
- 日志带轮 id：`[chat-xxxx]` / `[wake-xxxx]`（`app/trace.py`），一轮 grep 一个 id
- cache usage 日志**已有**：`nostos.llm` INFO 每次调用打 ms / attempts / 整个 usage
- 一道门：`ACCESS_TOKEN`（`app/auth.py`）。公网链接前必填；tailnet 内可空
- 测试：`pytest`（`server/tests/`）。拼装形状、刷子、护栏、开火原子、迁移、门、
  执行入口都有用例；改这几处先跑红再改绿，撤掉修复必须转红
- 备份还没做，上线前必须有（含定期真还原演习——「备份存在」不等于「备份能救命」）

详见 [PLAN_companion.md](./PLAN_companion.md)。
