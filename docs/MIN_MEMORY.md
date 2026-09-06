# Min-memory

长期记忆落在磁盘，不进向量库。

## 存哪

```
data/memories/<USER_ID>/
  index.json
  name.md
  hometown.md
  …
```

默认 `USER_ID=local`。`.gitignore` 已忽略 `data/*`（可导出备份）。

## 怎么进对话

1. 每轮 chat：把现有 md **召回**挂在消息列表末尾（`【当前记忆召回】`）。
2. 模型可调用内置 tool：`memory_list` / `memory_read` / `memory_write`（nostools 注册表）。
3. 写入后下一轮召回就能看见；也可打开 `/memories` 查看。

## 自测

1. `docker compose up --build`
2. 说：「我叫眠眠，住在上海，喜欢深夜写代码」
3. 打开 http://localhost:8787/memories 应有条目
4. 新开一句：「我住哪？」应能答上海（可清 SQLite 历史只留记忆再问，更狠）
