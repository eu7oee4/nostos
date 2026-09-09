"""Web Push 出站：伙伴到点开口那句，推到用户手机上。

**为什么这条重要**：PLAN §1 要证伪的第 2 条是「它主动来」，而且写明「第 2 条是分界线，
只做第 1 条做的还是一个更好的聊天机器人」。在这之前 wake 只往 `messages` 表写一条
assistant——用户不打开网页就永远看不见，假设 2 根本没在被测。

**为什么是 PWA 不是微信**（PLAN §4.1，09-09 查证）：微信客服消息只能在用户最近一次
互动后 48 小时内发，用户 48 小时没理你就够不着了——而那正是伙伴最该来找他的时候。
走标准 Web Push：**服务器直发浏览器厂商的 endpoint，不需要 Firebase 或任何第三方**，
中间没有能改规则的人。和 §7「不能把角色绑定在一个模型上」是同一条逻辑。

**硬前提是 HTTPS**：Service Worker 要安全上下文，`http://局域网IP` 和
`http://tailscale-IP` 都不行，SW 根本注册不起来。自托管用
`tailscale serve --bg http://127.0.0.1:8787` 拿 `*.ts.net` 真证书，仅 tailnet 可达。
2026-09-10 实测：iOS 16.4+ 的 PWA 在这种 origin 上能正常拿到 subscription，
endpoint 是 `web.push.apple.com`，`pywebpush` 直发拿到 201。

⚠️ **VAPID 的 `sub` 必须是合法 mailto:**：`py_vapid` 只收 mailto（`https:` 直接抛），
而 Apple 还校验域名——`mailto:…@localhost` 会被 403 `{"reason":"BadJwtToken"}` 拒掉，
状态码本身看不出原因，所以失败时把 response body 一起记进日志。

⚠️ **iOS 只在 standalone PWA 模式下给 Web Push**——用户必须「添加到主屏幕」再从
主屏图标打开，Safari 标签页里 `pushManager.subscribe()` 拿不到东西。
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from app import db
from app.config import settings

VAPID_KEY_NAME = "vapid_private.pem"
DEFAULT_VAPID_SUBJECT = "mailto:nostos@example.com"
MAX_BODY_CHARS = 120

log = logging.getLogger("nostos.push")


def vapid_key_path() -> Path:
    return Path(settings.data_dir) / VAPID_KEY_NAME


def _b64u(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _load_or_create_key() -> ec.EllipticCurvePrivateKey:
    """VAPID 密钥对，第一次用时自动生成到 data/。

    放 data/ 而不是环境变量：自托管的人 `docker compose up` 就该能跑，不该先手动
    生成一对密钥。**换掉这个文件等于换身份**——所有旧订阅立刻失效，用户得重新订阅。
    所以它跟着 data/ 一起备份。
    """
    path = vapid_key_path()
    if path.is_file():
        return serialization.load_pem_private_key(path.read_bytes(), password=None)

    key = ec.generate_private_key(ec.SECP256R1())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    path.chmod(0o600)
    log.info("generated VAPID keypair at %s", path)
    return key


def public_key_b64u() -> str:
    """前端 `pushManager.subscribe()` 要的 applicationServerKey（未压缩点，base64url）。"""
    pub = _load_or_create_key().public_key()
    return _b64u(
        pub.public_bytes(
            serialization.Encoding.X962,
            serialization.PublicFormat.UncompressedPoint,
        )
    )


def _send_one(sub: dict[str, Any], payload: str) -> int:
    """同步发一条。pywebpush 走 requests，必须扔进线程池，别阻塞事件循环。"""
    from pywebpush import WebPushException, webpush

    try:
        resp = webpush(
            subscription_info={
                "endpoint": sub["endpoint"],
                "keys": {"p256dh": sub["p256dh"], "auth": sub["auth"]},
            },
            data=payload,
            vapid_private_key=str(vapid_key_path()),
            vapid_claims={"sub": settings.vapid_subject},
        )
        return int(resp.status_code)
    except WebPushException as e:
        status = getattr(e.response, "status_code", 0) or 0
        # body 里的 reason 是唯一能分清「订阅过期」和「JWT 不对」的东西。只打状态码
        # 的话 403 看不出是 VAPID_SUBJECT 配错了（Apple: {"reason":"BadJwtToken"}）。
        detail = (getattr(e.response, "text", "") or str(e))[:200]
        log.warning(
            "push failed status=%s endpoint=%s… %s",
            status, sub["endpoint"][:40], detail,
        )
        return int(status)


async def notify(user_id: str, text: str, *, url: str = "/") -> dict[str, int]:
    """把一句话推给这个用户所有已订阅的设备。

    **绝不抛异常**：推送是 wake 的附加动作，站内那条 assistant 消息才是真相来源。
    推失败了对话照样在，用户打开网页仍然看得到——所以这里只记日志，不往上抛。
    """
    subs = await db.list_push_subscriptions(user_id)
    if not subs:
        return {"sent": 0, "failed": 0, "dropped": 0}

    if settings.vapid_subject == DEFAULT_VAPID_SUBJECT:
        # example.com 是占位域名。今天各家还收，但这本来就该是「联系得到运营者」的地址。
        log.warning(
            "VAPID_SUBJECT 还是默认的 %s —— 自托管请在 .env 里改成你自己的邮箱",
            DEFAULT_VAPID_SUBJECT,
        )

    body = text.strip().replace("\n", " ")
    if len(body) > MAX_BODY_CHARS:
        body = body[: MAX_BODY_CHARS - 1] + "…"
    payload = json.dumps(
        {"title": settings.push_title, "body": body, "url": url},
        ensure_ascii=False,
    )

    sent = failed = dropped = 0
    for sub in subs:
        try:
            status = await asyncio.to_thread(_send_one, sub, payload)
        except Exception as e:  # noqa: BLE001 — 推送不能弄挂 wake
            log.warning("push error endpoint=%s… %s", sub["endpoint"][:40], e)
            failed += 1
            continue

        if 200 <= status < 300:
            await db.mark_push_ok(sub["endpoint"])
            sent += 1
        elif status in (404, 410):
            # 订阅过期/被撤销。留着只会每次都失败，直接删。
            await db.delete_push_subscription(sub["endpoint"])
            dropped += 1
        else:
            failed += 1

    log.info(
        "push user=%s sent=%s failed=%s dropped=%s", user_id, sent, failed, dropped
    )
    return {"sent": sent, "failed": failed, "dropped": dropped}
