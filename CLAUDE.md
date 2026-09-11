# nostos

伙伴，不是助手。开工前先读 [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)，
末尾那节「施工纪律」是硬规矩，不要绕。

改这些地方之前，先读对应那份：

| 你要动 | 先读 |
|---|---|
| `server/app/context/`（拼装） | [docs/PROMPT_ASSEMBLY.md](docs/PROMPT_ASSEMBLY.md)——尤其「为什么要长得一样」。那几条是实测出来的，别顺手改回去 |
| `server/app/schedule/`（wake） | [docs/MIN_WAKE.md](docs/MIN_WAKE.md)。命名一律 **wake**，不叫 job。动 `policy.py` / 护栏再加一份 [docs/RANDOM_WAKE.md](docs/RANDOM_WAKE.md) |
| `server/app/memory/` | [docs/MIN_MEMORY.md](docs/MIN_MEMORY.md) |
| 产品取舍 / 该不该做这个功能 | [docs/PLAN_companion.md](docs/PLAN_companion.md)、[docs/DESIGN_prompt_assembly.md](docs/DESIGN_prompt_assembly.md) |

本地怎么跑：见 [README-zh.md](README-zh.md)。

改代码时同步改上面对应那份文档，**并跑 `pytest`**（`server/tests/`；改拼装 / 刷子 /
护栏 / wake 落库 / 门这几处先加用例再改）。没说的时候别 push。
