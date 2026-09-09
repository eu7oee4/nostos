# eval

Model-selection eval (offline). Not required to run nostos.

Will hold personas × scenarios × model matrix scoring — see cassette `companion/eval/` and PLAN §10.2.

Scaffold only; no runners yet.

---

## 纠偏探针：`prefs_write` 调用率

PLAN §10.2 那四个探针里的一个，2026-09-09 做 #9 时先量了。量的**不是回复好不好，是模型
肯不肯调 `prefs_write`**——不调，纠偏这轮之后就飘走了，而「说一句就变了」是 §5.2③ 拍板的
最强留存钩子。所以这条是选型的硬指标，不是锦上添花。

脚本：[`probe_prefs_write.py`](./probe_prefs_write.py)。换模型改 `.env` 的 `LLM_MODEL` 再跑。

### 第一批数据（`deepseek-chat`，2026-09-09）

一个场景 × 三种说法 × 每格 5 遍，`tool_choice="auto"`，工具给全 8 个（和聊天路径一致）：

| system prompt | 「你别这么客气，说短点」 | 「别老反问我」 | 「说话能不能自然点」 |
|---|---|---|---|
| 现状（规则在第 3 段） | 2/5 | 1/5 | 3/5 |
| 规则挪到末尾 | 0/5 | 2/5 | 3/5 |
| 现状 + 结尾再钉一句 | 3/5 | 1/5 | 5/5 |

**结论：改 prompt 最多推到 6 成，够不着 issue #9 的验收（「隔几轮还是短的」）。**
不是接线问题——同一套接线里 `memory_write` 一次就开火。是 `deepseek-chat` 认不出
「这句是在挑我说话方式」。

比「不调」更坏的一种形状：他会答「好，记住两个：别客气别反问」然后**什么都没写**。
下一段就飘了，而用户以为记住了。

### 09-09 的决定

**#9 按现状收，这条留给选型。** 不加词表前置筛 + 强制调用那层——那是把脚本塞进决策路径，
而且真正的问题是底座认不出来。等 §10.2 的模型矩阵跑起来，把这个数字当选型输入之一
（Haiku / Sonnet / Opus 各跑一遍，差多少直接决定值不值得为它换模型）。
