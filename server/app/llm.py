"""Direct HTTP chat completions (OpenAI-compatible). No agent SDK."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

import httpx

from app.config import settings

log = logging.getLogger("nostos.llm")

# 只重试一次、只重试「再来一次可能就好」的那类：限流、上游 5xx、连不上 / 超时。
# 4xx（除 429）是请求本身的问题，重试无意义。这个调用没有副作用（副作用全在
# tool handler 里、由 chat_loop 执行），所以重试是安全的。
_RETRY_STATUSES = {429, 500, 502, 503, 504}
_RETRY_DELAY_SECONDS = 1.5
_TIMEOUT_SECONDS = 120.0


class LLMError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"LLM HTTP {status}: {body[:500]}")


async def _post_once(url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise LLMError(resp.status_code, resp.text)
        return resp.json()


async def chat_completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = "auto",
) -> dict[str, Any]:
    """Return the assistant message object (may include tool_calls).

    每次调用记一行 INFO：耗时、有没有给工具、provider 返回的整个 usage（含缓存
    命中字段）。轮 id 由日志格式串自动带上（app/trace.py），所以一轮里调了几次、
    每次多少钱，grep 一个 id 全在。
    """
    if not settings.llm_api_key:
        raise LLMError(401, "LLM_API_KEY is empty — set it in .env")

    base = settings.llm_base_url.rstrip("/")
    url = f"{base}/chat/completions"
    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "stream": False,
    }
    if tools:
        payload["tools"] = tools
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice

    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }

    started = time.monotonic()
    attempts = 0
    while True:
        attempts += 1
        try:
            data = await _post_once(url, payload, headers)
            break
        except LLMError as e:
            if attempts >= 2 or e.status not in _RETRY_STATUSES:
                raise
            log.warning("llm HTTP %s, retrying once in %.1fs", e.status, _RETRY_DELAY_SECONDS)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            if attempts >= 2:
                raise LLMError(502, f"transport error: {e}") from e
            log.warning("llm transport error (%s), retrying once", type(e).__name__)
        await asyncio.sleep(_RETRY_DELAY_SECONDS)

    elapsed_ms = int((time.monotonic() - started) * 1000)
    usage = data.get("usage")
    log.info(
        "llm call ms=%s tools=%s attempts=%s usage %s",
        elapsed_ms,
        "yes" if tools else "no",
        attempts,
        usage if isinstance(usage, dict) else "missing",
    )

    try:
        return data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(502, f"unexpected response shape: {data!r}") from e
