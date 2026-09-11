"""一道门：`ACCESS_TOKEN`。

PLAN §4 定的是「每个内测用户发一个带 token 的链接，点开就是他自己的」。多用户
不在本公开仓做，但**单用户也需要这道门**：README 里 cloudflared / ngrok 那两条
路拿到的是公网链接，没门等于把 API key 挂在公网上任人烧。

`.env` 里 `ACCESS_TOKEN` 留空 = 门不存在（tailnet 内自托管默认这样，Tailscale
本身就是门）。填了之后，三种方式都认：

- 链接里带一次 `?token=…`：服务端种 cookie，之后这台设备的浏览器不用再带
- `Authorization: Bearer …`：curl / 脚本用
- cookie `nostos_token`：上面那条种下的

不检查的路径：Service Worker、manifest、图标。它们本来就是公开资源，而且浏览器
取 manifest 默认不带 cookie，拦了 PWA 就装不上。`/health` **要**检查——它露 user_id
和护栏设置。

比较用 `hmac.compare_digest`，别用 `==`。
"""

from __future__ import annotations

import hmac
import logging

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.config import settings

COOKIE_NAME = "nostos_token"
COOKIE_MAX_AGE = 365 * 24 * 3600

# 不设门的路径：公开静态资源。前缀匹配 icon-*.png。
_OPEN_PATHS = ("/sw.js", "/manifest.json")
_OPEN_PREFIXES = ("/icon-",)

log = logging.getLogger("nostos.auth")


def _presented(request: Request) -> str | None:
    auth = request.headers.get("authorization") or ""
    if auth.lower().startswith("bearer "):
        return auth[7:].strip() or None
    query = request.query_params.get("token")
    if query:
        return query
    return request.cookies.get(COOKIE_NAME) or None


def _matches(presented: str | None, expected: str) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(presented.encode("utf-8"), expected.encode("utf-8"))


def is_open_path(path: str) -> bool:
    return path in _OPEN_PATHS or path.startswith(_OPEN_PREFIXES)


async def access_gate(request: Request, call_next):
    expected = (settings.access_token or "").strip()
    if not expected or is_open_path(request.url.path):
        return await call_next(request)

    presented = _presented(request)
    if not _matches(presented, expected):
        log.warning(
            "denied %s %s from %s",
            request.method,
            request.url.path,
            request.client.host if request.client else "?",
        )
        return JSONResponse(
            {"detail": "access token required (ACCESS_TOKEN is set on this server)"},
            status_code=401,
        )

    # 链接里带的那次：种 cookie，然后把 token 从地址栏里去掉（别留在历史记录 /
    # 截图里）。Secure 只在 https 时加，否则 http://localhost 自测时 cookie 种不上。
    if request.query_params.get("token"):
        clean = request.url.remove_query_params("token")
        response = RedirectResponse(str(clean), status_code=303)
        response.set_cookie(
            COOKIE_NAME,
            expected,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
            secure=request.url.scheme == "https",
        )
        return response

    return await call_next(request)
