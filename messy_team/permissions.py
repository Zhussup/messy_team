"""Permission decisions for tool calls.

Chat modes (per chat, stored in the database):
- "ask"      — writing tools require one-off approval (default)
- "allow"    — every tool runs without approval
- "readonly" — writing tools are denied outright

Read-only tools never require approval, and "always in this chat" grants are
remembered in memory for the running app only.
"""

from __future__ import annotations

READ_TOOLS = frozenset({"read_file", "ls", "glob", "grep"})
WRITE_TOOLS = frozenset({"write_file", "edit_file", "bash"})

ALLOW = "allow"
ASK = "ask"
DENY = "deny"


class Permissions:
    def __init__(self) -> None:
        self._session_grants: dict[int, set[str]] = {}

    def decide(self, chat_id: int, chat_mode: str, tool: str) -> str:
        if tool in READ_TOOLS:
            return ALLOW
        if chat_mode == "allow":
            return ALLOW
        if tool in self._session_grants.get(chat_id, set()):
            return ALLOW
        if chat_mode == "readonly":
            return DENY
        return ASK

    def grant(self, chat_id: int, tool: str) -> None:
        self._session_grants.setdefault(chat_id, set()).add(tool)
