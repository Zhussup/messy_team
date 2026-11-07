"""Async client for the Ollama Cloud native API (NDJSON streaming chat)."""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

import httpx

# Cloud chat can think for a long time; give the read a generous budget.
# Rate limits get one automatic retry (429 arrives before any output).


class ApiError(Exception):
    """Any error reported by the API or the transport."""

    def __init__(self, message: str, retry_after: int | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class OllamaClient:
    def __init__(
        self,
        api_base: str,
        api_key: str | None,
        timeout: httpx.Timeout | None = None,
    ) -> None:
        if not api_key:
            raise ApiError(
                "No API key: set the OLLAMA_API_KEY environment variable"
                " or `api_key` in ~/.config/messy-team/config.toml"
            )
        self._url = api_base.rstrip("/") + "/chat"
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        self._timeout = timeout or httpx.Timeout(20.0, read=600.0)

    async def list_models(self) -> list[str]:
        """Model names from GET /api/tags, sorted (the catalog of cloud models)."""
        url = self._url.rsplit("/", 1)[0] + "/tags"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=self._headers)
        if resp.status_code != 200:
            raise ApiError(f"HTTP {resp.status_code} listing models")
        models = resp.json().get("models") or []
        return sorted(str(m.get("model") or m.get("name") or "") for m in models if m)

    async def balance(self) -> dict[str, Any]:
        """Raw balance payload from GET /api/balance (rate limit: 10/min)."""
        url = self._url.rsplit("/", 1)[0] + "/balance"
        async with httpx.AsyncClient(timeout=20.0) as client:
            resp = await client.get(url, headers=self._headers)
        if resp.status_code == 429:
            retry = resp.headers.get("Retry-After")
            raise ApiError(
                "Rate limited (429)" + (f", retry after {retry}s" if retry else ""),
                retry_after=int(retry) if retry and retry.isdigit() else None,
            )
        if resp.status_code != 200:
            raise ApiError(f"HTTP {resp.status_code} fetching balance")
        return resp.json()

    async def stream_chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """Yield events: {"type": "thinking"|"content"|"tool_calls", ...} and
        a final {"type": "done", "message": {...}}.

        Raises ApiError on transport errors, non-200 responses (429 carries
        Retry-After) and mid-stream `{"error": ...}` lines. A 429 before any
        output is retried automatically a couple of times.
        """
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
        if tools:
            payload["tools"] = tools
        for attempt in range(3):
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    async with client.stream(
                        "POST", self._url, json=payload, headers=self._headers
                    ) as resp:
                        if resp.status_code == 429:
                            retry = resp.headers.get("Retry-After")
                            retry_after = (
                                int(retry) if retry and retry.isdigit() else None
                            )
                            if attempt < 2:
                                delay = retry_after or (2, 8)[attempt]
                                await asyncio.sleep(min(float(delay), 60.0))
                                continue
                            raise ApiError(
                                "Rate limited (429)"
                                + (f", retry after {retry}s" if retry else ""),
                                retry_after=retry_after,
                            )
                        if resp.status_code != 200:
                            body = (await resp.aread()).decode(errors="replace")
                            raise ApiError(f"HTTP {resp.status_code}: {body[:300]}")
                        content = ""
                        thinking = ""
                        tool_calls: list[dict[str, Any]] = []
                        async for line in resp.aiter_lines():
                            line = line.strip()
                            if not line:
                                continue
                            try:
                                chunk = json.loads(line)
                            except json.JSONDecodeError:
                                continue
                            if "error" in chunk:
                                raise ApiError(str(chunk["error"]))
                            message = chunk.get("message") or {}
                            delta = message.get("thinking")
                            if delta:
                                thinking += delta
                                yield {"type": "thinking", "text": delta}
                            delta = message.get("content")
                            if delta:
                                content += delta
                                yield {"type": "content", "text": delta}
                            if message.get("tool_calls"):
                                # The API sends the complete tool_calls on one chunk.
                                tool_calls = message["tool_calls"]
                                yield {"type": "tool_calls", "tool_calls": tool_calls}
                            if chunk.get("done"):
                                yield {
                                    "type": "done",
                                    "message": {
                                        "role": "assistant",
                                        "content": content,
                                        "thinking": thinking,
                                        "tool_calls": tool_calls,
                                    },
                                }
                                return
                        raise ApiError("Stream ended without a done chunk")
            except httpx.HTTPError as e:
                raise ApiError(f"Network error: {e}") from e
