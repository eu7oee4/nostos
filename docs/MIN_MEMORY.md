# Min-memory

长期记忆落在磁盘，不进向量库。设计定稿在 Notion「nostos 记忆系统设计对照（EverOS / 蛋壳）」，
落地顺序第 1 步（①⑩⑫）2026-09-14 落了，本页照那份改。

## 存哪

```
data/memories/<USER_ID>/
  index.json
  name.md
  hometown.md
  mom-tension.md
  …
```

默认 `USER_ID=local`。`.gitignore` 已忽略 `data/*`（可导出备份）。

**md 是唯一真源**，`index.json` 只记元数据，整个删掉重建也不丢记忆：

```json
{ "id": "mom-tension", "title": "和妈妈相处会紧张", "kind": "feel",
  "intensity": "mid", "updated_at": "2026-09-03T02:00:01Z" }
```

- `kind`：`item`（事件 / 短事实）或 `feel`（感受）。index 里没这个字段的老文件一律按 `item`
- `intensity`：只有 feel 有，`low` / `mid` / `high` 三档。**这是占位**：实测 deepseek-chat 只会给 mid
  （`eval/README.md` 第三轮），机主 09-14 定三档太粗暴、后面要改，召回端消费 intensity 一起做（落地顺序第 4 步）。
  现在召回端不读这个字段，只在标题里显示
- 以后检索上线（落地顺序第 3 步）再加 `sha256` 和向量，这一版没有

## 两种记忆，两个写工具（2026-09-14，Notion ⑫）

| 工具 | 写什么 | 例子 |
|---|---|---|
| `memory_write_item(name, content)` | 事件、短事实：人、地方、工作、计划、日期 | 「妈妈 9 月中旬来杭州小住」 |
| `memory_write_feel(name, content, intensity)` | 感受，带浓度 | 「和妈妈长时间相处会紧张」，浓度中 |

「我妈下周来杭州住几天，有点紧张」这一句会落成两条，两次调用，记完不宣布。

**为什么是两个工具而不是一个工具加 `kind` 参数**：prefs_write 那次实验（issue #9）证明了
工具描述文本是唯一的杠杆，两个描述各自把触发例句写满，统计也天然分开。kind 在写入端就定了：
选工具就是选 kind。

**风险**：feel 和 prefs_write 是同一种难题，都要在对话中认出「这句属于某个元类别」再停下来
调工具。事件那路不担心（`memory_write` 对 name / hometown 一次就开火），感受那路可能重演 #9。
所以有 [`eval/probe_memory_feel.py`](../eval/probe_memory_feel.py) 这根探针，数据在
[`eval/README.md`](../eval/README.md)。feel 开火率长期接近零而对话里明明有情绪，兜底的形状
见 Notion ⑨：提炼时把本段已写的条目列给模型，只补漏掉的。

`memory_write` 这个老名字保留一个版本周期当 item 的别名：`chat_loop._run_tool` 还认它，
但**不在给模型看的 `CHAT_TOOL_NAMES` 里**——三个写工具摆在一起他会挑最短的那个。

模型只有 write / read / list，**没有 `memory_delete`**。删除是用户的隐私出口，和 prefs 不给
模型 delete 是同一条纪律。

## 怎么进对话

1. 每轮 chat：把现有 md **召回**注入一条 system 消息，位置在**对话历史之后、
   当前轮之前**（断点③之后，每轮可变）。没有记忆时整块省略，不留占位（发言权原则：
   没注入到眼前的东西不许评论）。拼装全貌见 [PROMPT_ASSEMBLY.md](./PROMPT_ASSEMBLY.md)。
2. **新的在前，每条带日期**（Notion ①）。`list_memories` 按 `updated_at` 倒序（没索引的按
   mtime），`recall_text` 每块标题带「记于 MM-DD」（跨年才带年份），feel 还带浓度；块开头
   一句边界说明。检索上线前这是唯一排序规则，上线后作为同分时的次序。
   原来按文件名字母序拼接：z 开头的记忆永远最先被截掉，而且模型完全不知道一条是什么时候
   记的——10 月看到「妈妈 9 月中旬来杭州」会当成现在的事，问「你妈到了吗」。

   ```
   现在浮现在你脑海里的记忆有：
   （每条都标了记下的日期，新的在前。那是当时的事，不一定是现在的情况。）

   ### 和妈妈相处会紧张 (`mom-tension`) | 感受，浓度中 | 记于 09-03
   # 和妈妈相处会紧张
   …
   ### 妈妈来杭州 (`mom-visit`) | 记于 09-03
   …
   ```
3. 预算仍是 6000 字符这个量级，记忆是配菜不是主食。超预算从最旧那头砍。
4. 写入后下一轮召回就能看见。

## 用户出口（Notion ⑩）

- `GET /memories` 列表（带 kind / intensity / updated_at，新的在前）、`GET /memories/{id}` 看一条
- `DELETE /memories/{id}` 删一条：md 和 index 条目一起删
- 网页右上角「记忆」抽屉，照「说话方式」那个做：每条一行，标题 + 事件/感受·浓度 + 日期 + id，
  右边「删除」

## 观测

- 每次写入 `nostos.memory` INFO 一行：`memory written kind=feel intensity=mid id=mom-tension`
- 轮末 `nostos.chat` 那行汇总多了 `mem_item=N mem_feel=N`（Notion ⑦「每段的 item / feel 写入数」，
  先按轮记，日志里同一行带 `segment=<id>`，按段汇总 grep 它）。feel 长期为零而对话里明明有
  情绪，就是该加提炼兜底的信号
- `data/episodes/<USER_ID>/seg-<段id>.md` 是另一种东西：角色自己写的回忆（[SEGMENTS.md](./SEGMENTS.md)），
  可看不可改，不进 index、不进召回。和这里的 item / feel 条目是两个维度（PLAN §4.2「两种归属」）

## 自测

1. `docker compose up --build`
2. 说：「我叫眠眠，住在上海，喜欢深夜写代码」
3. 打开 http://localhost:8787/memories 应有条目；网页右上角「记忆」也能看到
4. 说：「我妈下周来杭州住几天，有点紧张，我俩很久没长时间相处了」——理想是一条 item 加一条 feel，
   日志里 `mem_item=1 mem_feel=1`。feel 没开火不奇怪，探针数据见 `eval/README.md`
5. 新开一句：「我住哪？」应能答上海（可清 SQLite 历史只留记忆再问，更狠）
6. 抽屉里删掉一条，再问，他应该不知道了
