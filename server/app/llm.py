"""Direct HTTP chat completions (OpenAI-compatible). No agent SDK."""

from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


class LLMError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"LLM HTTP {status}: {body[:500]}")


async def chat_completion(messages: list[dict[str, str]]) -> str:
    if not settings.llm_api_key:
        raise LLMError(401, "LLM_API_KEY is empty — set it in .env")

    base = settings.llm_base_url.rstrip("/")
    url = f"{base}/chat/completions"
    payload: dict[str, Any] = {
        "model": settings.llm_model,
        "messages": messages,
        "stream": False,
    }
    headers = {
        "Authorization": f"Bearer {settings.llm_api_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        resp = await client.post(url, json=payload, headers=headers)
        if resp.status_code >= 400:
            raise LLMError(resp.status_code, resp.text)
        data = resp.json()

    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise LLMError(502, f"unexpected response shape: {data!r}") from e
