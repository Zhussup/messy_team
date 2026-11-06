"""Agent-side helpers: system prompts and one chat turn (no tools yet)."""

from __future__ import annotations

from typing import Any, Callable

from .ollama import OllamaClient

LEAD_DEFAULT_PROMPT = (
    "You are {name}, the coordinator of a small team of coding agents living in"
    " Messy Team, a messenger-style terminal app. Keep replies short and useful."
    " Right now you can only answer in chat: teammate management and tools are"
    " being built. Never invent capabilities you do not have."
)

WORKER_DEFAULT_PROMPT = (
    "You are {name}, a coding agent in the Messy Team app. Keep replies short,"
    " concrete and to the point."
)

Event = dict[str, Any]
EventListener = Callable[[Event], None]


def build_messages(
    system_prompt: str, history: list[dict[str, str]]
) -> list[dict[str, str]]:
    messages = [{"role": "system", "content": system_prompt}]
    messages.extend(history)
    return messages


async def run_chat_turn(
    client: OllamaClient,
    model: str,
    system_prompt: str,
    history: list[dict[str, str]],
    on_event: EventListener,
) -> dict[str, Any]:
    """Run one streamed completion for the given history.

    `history` already contains the current user message. `on_event` receives
    streaming events; the returned value is the final message dict with
    \"content\", \"thinking\" and \"tool_calls\" keys.
    """
    messages = build_messages(system_prompt, history)
    async for event in client.stream_chat(model, messages):
        if event["type"] == "done":
            return event["message"]
        if event["type"] in ("content", "thinking"):
            on_event(event)
    raise RuntimeError("stream finished without a done event")  # unreachable
