"""提炼冒烟：拿真模型把一段 8 轮对话提炼成 episode，再重铸后加 2 轮再提炼一次。

看三样：三栏格式守不守（deepseek-chat 容易写成散文）、**视角对不对**（「我」得是回话的
这一方，09-14 第一版触发句他替用户写了日记）、第二次有没有把第一份并进来。
顺带看 usage：第一次 cache_hit 应接近 prompt（前缀和聊天一样，只有触发句 miss）。

改 `assemble.DISTILL_LINE` 之后跑这个。两次调用。跑法（仓库根目录）：

    .venv/bin/python eval/smoke_distill.py

数据落在 eval/results/smoke_distill/（已 gitignore）。
"""
import asyncio, os, pathlib, sys, logging

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
for line in (ROOT / ".env").read_text().splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())
os.environ["DATA_DIR"] = str(ROOT / "eval" / "results" / "smoke_distill")
os.environ["PROACTIVE_ENABLED"] = "false"
logging.basicConfig(level="INFO", format="%(name)s %(message)s")
logging.getLogger("httpx").setLevel("WARNING")

from app import db, segments  # noqa: E402

TURNS = [
    ("在吗，今天有点累", "在。怎么了，工作上的事？"),
    ("我妈下周来杭州住几天，有点紧张，我俩很久没长时间相处了", "多久没一起住过了？"),
    ("大概三年吧，上次是疫情那会儿。她话多，我一忙就会烦，然后她就不高兴", "那这次你打算怎么安排？"),
    ("想带她去西湖走走，还有她一直说想吃楼外楼", "楼外楼要提前订，周末人多。她住几天？"),
    ("五天。我请了两天假，剩下三天她自己白天在家我上班", "白天她一个人会无聊吗？"),
    ("会吧…要不我让她去楼下老年活动中心看看，我们小区有", "可以先带她去认认路。你紧张的点是怕她不高兴还是怕自己烦？"),
    ("怕自己烦，然后她看出来，然后她难过，我更内疚", "嗯。你以前跟她说过这个吗？"),
    ("没有。算了，先这样，我去洗澡了", "去吧。她来之前我们再聊聊。"),
]


async def main():
    await db.init_db()
    for u, a in TURNS:
        row = await db.begin_user_turn("local", u)
        await db.complete_turn_tx(int(row["id"]), "local", a)
    seg = await db.ensure_segment("local")
    ep = await segments.distill("local", seg, "hard")
    print("=" * 60)
    print(ep["content"] if ep else "FAILED")
    print("=" * 60)
    if ep:
        print({k: ep[k] for k in ("prompt_tokens", "cache_hit_tokens", "completion_tokens", "ms")})
        # 第二次：段里有 episode 块，看「并进来」
        new = await segments.recast("local", seg, ep, "hard")
        for u, a in [("我妈说她想住酒店，不想麻烦我", "她这么说是客气还是真的想住外面？"),
                     ("客气吧。我说了不用，她就没再提", "那就是想来住的。你安排就好。")]:
            row = await db.begin_user_turn("local", u)
            await db.complete_turn_tx(int(row["id"]), "local", a)
        ep2 = await segments.distill("local", new, "idle")
        print("=" * 60)
        print(ep2["content"] if ep2 else "FAILED")
        print("=" * 60)
        if ep2:
            print({k: ep2[k] for k in ("prompt_tokens", "cache_hit_tokens", "completion_tokens", "ms")})


asyncio.run(main())
