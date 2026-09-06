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
- 其余能力默认关；出站副作用：人批 + 留痕（stub）
- 凭证按 `user_id` 隔离，不进 prompt
- 不建商店 / 热加载 / browser sidecar

## 施工纪律

- 第一天就有 `user_id`，无「可变当前用户」全局
- 时间戳服务端盖、不进 system；召回挂当轮尾
- 动作走 tool_use，不走正文标记
- 备份与 cache usage 日志后续补，上线前必须有

详见 [PLAN_companion.md](./PLAN_companion.md)。
