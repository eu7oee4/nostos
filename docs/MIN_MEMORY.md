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

1. 每轮 chat：把现有 md **召回**注入一条 system 消息，位置在**对话历史之后、
   当前轮之前**，开头是「现在浮现在你脑海里的记忆有：」。没有记忆时整块省略，
   不留占位（发言权原则：没注入到眼前的东西不许评论）。拼装全貌见
   [PROMPT_ASSEMBLY.md](./PROMPT_ASSEMBLY.md)。
2. 模型可调用内置 tool：`memory_list` / `memory_read` / `memory_write`（nostools 注册表）。
3. 写入后下一轮召回就能看见；也可打开 `/memories` 查看。

## 自测

1. `docker compose up --build`
2. 说：「我叫眠眠，住在上海，喜欢深夜写代码」
3. 打开 http://localhost:8787/memories 应有条目
4. 新开一句：「我住哪？」应能答上海（可清 SQLite 历史只留记忆再问，更狠）
