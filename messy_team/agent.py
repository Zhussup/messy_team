"""Agent-side helpers: system prompts and the streamed tool-calling loop."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from .ollama import OllamaClient

LEAD_DEFAULT_PROMPT = (
    "You are {name}, the coordinator of a small team of coding agents living in"
    " Messy Team, a messenger-style terminal app. Keep replies short and useful."
    " You can create teammates and delegate tasks (being built). Never invent"
    " capabilities you do not have."
)

WORKER_DEFAULT_PROMPT = (
    "You are {name}, a coding agent in the Messy Team app. Keep replies short,"
    " concrete and to the point."
)

TOOL_INSTRUCTIONS = (
    "You are a coding agent whose tools act inside the session workspace:"
    " {workspace}. Prefer tools over guessing - read files before editing,"
    " verify results with commands. Paths are workspace-relative. Keep chat"
    " replies short; use tools for anything involving files or commands."
)

Event = dict[str, Any]

# on_tool_call(call) -> async result string for the tool message.
ToolCaller = Callable[[dict[str, Any]], Awaitable[str]]
EventListener = Callable[[Event], None]


def build_messages(
    system_prompt: str, history: list[dict[str, str]]
) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    return messages


async def stream_once(
    client: OllamaClient,
    model: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
    on_event: EventListener,
) -> dict[str, Any]:
    """One streamed completion; returns the final message dict.

    Forwards thinking/content deltas to on_event.
    """
    async for event in client.stream_chat(model, messages, tools=tools):
        if event["type"] == "done":
            return event["message"]
        if event["type"] in ("content", "thinking"):
            on_event(event)
    raise RuntimeError("stream finished without a done event")  # pragma: no cover


def _normalize_calls(message: dict[str, Any]) -> list[dict[str, Any]]:
    calls = []
    for call in message.get("tool_calls") or []:
        function = call.get("function") or {}
        arguments = function.get("arguments")
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {"_raw": arguments}
        if not isinstance(arguments, dict):
            arguments = {}
        calls.append({"function": {
            "name": function.get("name", "unknown"),
            "arguments": arguments,
        }})
    return calls


async def run_agent_turn(
    client: OllamaClient,
    model: str,
    system_prompt: str,
    history: list[dict[str, str]],
    tools: list[dict[str, Any]] | None,
    on_event: EventListener,
    on_tool_call: ToolCaller,
    max_rounds: int = 25,
) -> dict[str, Any]:
    """A full agent turn: stream, run requested tools, repeat until a plain
    answer. Emits a \"round_end\" event (with content/thinking/tool_calls)
    after each round that asked for tools. Returns the final message; the
    key \"budget_hit\" marks a turn cut off by max_rounds.
    """
    messages = build_messages(system_prompt, history)
    for _ in range(max_rounds):
        final = await stream_once(client, model, messages, tools, on_event)
        calls = _normalize_calls(final)
        if not calls:
            final["budget_hit"] = False
            return final
        on_event({
            "type": "round_end",
            "content": final.get("content", ""),
            "thinking": final.get("thinking", ""),
            "tool_calls": calls,
        })
        messages.append({
            "role": "assistant",
            "content": final.get("content", ""),
            "tool_calls": calls,
        })
        for call in calls:
            result = await on_tool_call(call)
            messages.append({"role": "tool", "content": result})
    final = await stream_once(client, model, messages, tools, on_event)
    final["budget_hit"] = bool(final.get("tool_calls"))
    if not final.get("tool_calls"):
        messages.append({"role": "assistant", "content": final.get("content", "")})
    return final
