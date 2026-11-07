"""Agent-side helpers: system prompts and the streamed tool-calling loop."""

from __future__ import annotations

import json
from typing import Any, Awaitable, Callable

from .ollama import OllamaClient
from .tools import TOOLS_SPEC

LEAD_DEFAULT_PROMPT = (
    "You are {name}, the coordinator of a small team of coding agents living in"
    " Messy Team, a messenger-style terminal app. Keep replies short and useful."
    " You can create teammates with create_agent and hand them tasks with"
    " assign_task. After organizing, tell the user briefly what you set up and"
    " end your turn - teammates work and report back automatically; their"
    " messages arrive in your chats. Never invent capabilities you do not have."
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

TEAM_INSTRUCTIONS = (
    " You have teammates living in this app: {teammates}. To contact one, call"
    " send_message(to_agent, text, private): a public message appears in the"
    " group chat you are in; a private message opens in the teammate's direct"
    " chat. Use it for real handoffs, not small talk; one call per message."
)

MANAGER_INSTRUCTIONS = (
    " As the team manager you have three people tools. create_agent spawns a"
    " new teammate (default model unless `model` is given; `instructions`"
    " become part of their prompt). create_group(title, agents) opens a group"
    " chat. assign_task(to_agent, task) writes the task into a teammate's"
    " direct chat and they start working. When the user asks you to organize"
    " or autonomously execute something: create the teammates you need, assign"
    " concrete tasks with assign_task, and end your turn with a short status"
    " note. Do not run bash or file tools to check on teammates and never do"
    " their file work yourself - teammate reports arrive automatically as new"
    " messages, and only then you reply to the user."
)

AGENT_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_agent",
        "description": "Spawn a new teammate agent with a DM chat.",
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Teammate name"},
                "model": {"type": "string",
                          "description": "Model name, optional"},
                "instructions": {"type": "string",
                                 "description": "Extra prompt for them, optional"},
            },
            "required": ["name"],
        },
    },
}

GROUP_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "create_group",
        "description": "Open a group chat with the named teammates (and you).",
        "parameters": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "agents": {"type": "array", "items": {"type": "string"},
                           "description": "Teammate names"},
            },
            "required": ["title", "agents"],
        },
    },
}

TASK_TOOL_SCHEMA = {
    "type": "function",
    "function": {
        "name": "assign_task",
        "description": "Give a teammate a concrete task in their direct chat.",
        "parameters": {
            "type": "object",
            "properties": {
                "to_agent": {"type": "string"},
                "task": {"type": "string",
                         "description": "Precise task description"},
            },
            "required": ["to_agent", "task"],
        },
    },
}

MANAGER_TOOLS_SPEC = [AGENT_TOOL_SCHEMA, GROUP_TOOL_SCHEMA, TASK_TOOL_SCHEMA]

CHAT_TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "send_message",
            "description": (
                "Send a message to a teammate agent. public=false + group chat"
                " shows it in the group and they answer there; private=true"
                " sends it to their direct chat."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "to_agent": {"type": "string",
                                 "description": "Exact teammate name"},
                    "text": {"type": "string"},
                    "private": {"type": "boolean"},
                },
                "required": ["to_agent", "text", "private"],
            },
        },
    }
]

AGENT_TOOLS_SPEC = TOOLS_SPEC + CHAT_TOOLS_SPEC

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
