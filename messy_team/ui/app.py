"""The Messy Team app: sidebar with chats, feed, composer."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.message import Message
from textual.widgets import Footer, Header, ListItem, ListView, Static, TextArea

from ..agent import (
    AGENT_TOOLS_SPEC,
    CHAT_TOOLS_SPEC,
    LEAD_DEFAULT_PROMPT,
    MANAGER_INSTRUCTIONS,
    MANAGER_TOOLS_SPEC,
    TEAM_INSTRUCTIONS,
    TOOL_INSTRUCTIONS,
    WORKER_DEFAULT_PROMPT,
    run_agent_turn,
)
from ..config import Config
from ..db import Database
from ..ollama import ApiError, OllamaClient
from ..permissions import ASK, ALLOW, DENY, Permissions
from ..tools import ToolError, ToolExecutor
from .modals import AgentManagerModal, GroupModal, PermissionModal, SessionModal
from .widgets import MessageView, StreamingMessage, ToolCard


class Composer(TextArea):
    """Message input: Enter sends, Shift+Enter inserts a newline."""

    @dataclass
    class Submitted(Message):
        value: str

    def _on_key(self, event) -> None:  # events.Key
        if event.key == "enter":
            event.stop()
            event.prevent_default()
            value = self.text
            self.clear()
            self.post_message(self.Submitted(value))
        elif event.key == "shift+enter":
            event.stop()
            event.prevent_default()
            self.insert("\n")


class MessyTeamApp(App[None]):
    TITLE = "Messy Team"
    CSS = """
    #main { height: 1fr; }
    #sidebar {
        width: 36; padding: 0 1; border-right: round $surface-lighten-2;
    }
    #session-title { color: $text-muted; text-style: italic; margin-bottom: 1; }
    #chats { height: 1fr; }
    #chat { height: 1fr; }
    #chat-title { padding: 0 2; border-bottom: round $surface-lighten-2; }
    #feed { height: 1fr; padding: 0 2; }
    #composer { min-height: 3; max-height: 10; border: round $accent; }
    .chat-label { padding: 0 1; }
    .msg { margin-top: 1; }
    .msg .who { color: $text-muted; text-style: bold; }
    .msg .who.who-me { color: $success; }
    .msg .who.who-agent { color: $primary; }
    .msg.streaming .who { color: $warning; }
    .msg .thinking { color: $text-disabled; text-style: italic; }
    .msg .thinking.live { color: $warning; }
    .msg.errored .body { color: $error; }
    .notice { color: $warning; margin: 1 2; }
    """

    BINDINGS = [
        Binding("ctrl+s", "sessions", "Sessions"),
        Binding("ctrl+n", "agents", "Agents"),
        Binding("ctrl+p", "tools_mode", "Permission mode"),
        Binding("ctrl+q", "quit", "Quit", priority=True),
        Binding("ctrl+l", "feed_to_bottom", "Scroll to bottom"),
    ]

    def __init__(self, config: Config) -> None:
        super().__init__()
        self.config = config
        self.db = Database(config.db_path)
        self._client: OllamaClient | None = None
        self.session_id: int | None = None
        self.chat_id: int | None = None
        self._open_stream: StreamingMessage | None = None
        self.permissions = Permissions()

    # -- client ------------------------------------------------------------

    def _require_client(self) -> OllamaClient:
        if self._client is None:
            self._client = OllamaClient(self.config.api_base, self.config.api_key)
        return self._client

    # -- layout ------------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="main"):
            with Vertical(id="sidebar"):
                yield Static("", id="session-title")
                yield ListView(id="chats")
            with Vertical(id="chat"):
                yield Static("", id="chat-title")
                yield ScrollableContainer(id="feed")
                yield Composer(
                    id="composer",
                    placeholder="Type a message — Enter to send, Shift+Enter for a newline",
                    soft_wrap=True,
                )
        yield Footer()

    def on_mount(self) -> None:
        sessions = self.db.list_sessions()
        if not sessions:
            self._open_session_modal(initial=True)
        else:
            self._load_session(sessions[0]["id"])
        self.query_one("#composer", Composer).focus()

    # -- sessions ----------------------------------------------------------

    def action_sessions(self) -> None:
        self._open_session_modal(initial=self.session_id is None)

    def _open_session_modal(self, initial: bool) -> None:
        self.push_screen(
            SessionModal(self.db.list_sessions(), can_cancel=not initial),
            self._on_session_choice,
        )

    def _on_session_choice(self, choice) -> None:
        if choice is None:
            self.query_one("#composer", Composer).focus()
            return
        if choice[0] == "open":
            self._load_session(choice[1])
        else:
            _, title, cwd = choice
            session_id = self._create_session(title, cwd)
            self._load_session(session_id)

    def _create_session(self, title: str, cwd: str) -> int:
        """A fresh session gets the coordinator agent Lead and a DM with it."""
        session_id = self.db.create_session(title, cwd)
        agent_id = self.db.create_agent(
            session_id,
            name="Lead",
            model=self.config.worker_model,
            system_prompt=LEAD_DEFAULT_PROMPT.format(name="Lead"),
            kind="manager",
        )
        self.db.create_chat(
            session_id, "dm", title="Lead", dm_agent_id=agent_id
        )
        return session_id

    def _load_session(self, session_id: int) -> None:
        self._abort_stream()
        self.session_id = session_id
        session = self.db.get_session(session_id)
        self.query_one("#session-title", Static).update(
            f"{session['title']}  ·  {session['cwd']}"
        )
        self._refresh_chats()
        chats = self.db.list_chats(session_id)
        if chats:
            self._open_chat(chats[0]["id"])
        else:
            self.chat_id = None
            self._clear_feed()
        self.query_one("#composer", Composer).focus()

    def _refresh_chats(self) -> None:
        chats_list = self.query_one("#chats", ListView)
        chats_list.clear()
        for chat in self.db.list_chats(self.session_id):
            icon = "·" if chat["kind"] == "dm" else "#"
            item = ListItem(
                Static(f"{icon} {chat['title']}", classes="chat-label")
            )
            item.chat_id = chat["id"]
            chats_list.append(item)

    @on(ListView.Selected, "#chats")
    def _chat_selected(self, event: ListView.Selected) -> None:
        self._open_chat(event.item.chat_id)

    # -- feed / chat -------------------------------------------------------

    def _feed(self) -> ScrollableContainer:
        return self.query_one("#feed", ScrollableContainer)

    def _clear_feed(self) -> None:
        feed = self._feed()
        feed.remove_children()
        self.query_one("#chat-title", Static).update("no chat open")

    def _open_chat(self, chat_id: int) -> None:
        self._abort_stream()
        self.chat_id = chat_id
        self._chat_title_refresh()
        feed = self._feed()
        feed.remove_children()
        for message in self.db.list_messages(chat_id):
            view = self._view_for_message(message)
            feed.mount(view)
            if message["is_tool_round"]:
                for call in self.db.list_tool_calls(message["id"]):
                    if call["name"] == "send_message":
                        continue  # visible as the message itself
                    feed.mount(ToolCard.from_record(call))
        feed.scroll_end(animate=False)

    def _chat_title_refresh(self) -> None:
        if self.chat_id is None:
            return
        chat = self.db.get_chat(self.chat_id)
        icon = "@" if chat["kind"] == "dm" else "#"
        labels = {
            "ask": "tools: ask",
            "allow": "tools: allow all",
            "readonly": "tools: read-only",
        }
        label = labels.get(chat["perm_mode"], "tools: ask")
        self.query_one("#chat-title", Static).update(
            f"{icon} {chat['title']}  ·  {label}"
        )

    def action_tools_mode(self) -> None:
        """Ctrl+P: cycle the current chat's permission mode."""
        if self.chat_id is None:
            self.notify("Open a chat first", severity="warning")
            return
        chat = self.db.get_chat(self.chat_id)
        order = ["ask", "allow", "readonly"]
        current = chat["perm_mode"] if chat["perm_mode"] in order else "ask"
        mode = order[(order.index(current) + 1) % len(order)]
        self.db.set_chat_perm_mode(self.chat_id, mode)
        self._chat_title_refresh()
        self.notify(f"Tool permission mode: {mode}")

    def _view_for_message(self, message) -> MessageView:
        if message["author"] == "user":
            view = MessageView("You", classes="me")
        else:
            view = MessageView(
                message["agent_name"] or "agent", model=message["agent_model"],
                classes="agent",
            )
            if message["thinking"]:
                view.set_thinking(live=False, text="✻ thought for a bit")
        view.set_body(message["content"])
        return view

    # -- agents ------------------------------------------------------------

    def action_agents(self) -> None:
        if self.session_id is None:
            self.notify("Open a session first (Ctrl+S)", severity="warning")
            return
        self.push_screen(
            AgentManagerModal(self.db.list_agents(self.session_id),
                              on_change=self._on_agent_edit)
        )

    def _on_agent_edit(self, result) -> None:
        import sqlite3 as _sqlite3

        if not result:
            return
        kind = result[0]
        if kind == "create":
            _, name, model, prompt = result
            try:
                agent_id = self.db.create_agent(
                    self.session_id, name, model, prompt or
                    WORKER_DEFAULT_PROMPT.format(name=name)
                )
            except _sqlite3.IntegrityError:
                self.notify(f"Agent named {name!r} already exists", severity="error")
                return
            self.db.get_or_create_dm(self.session_id, agent_id, name)
            self.notify(f"Agent {name} created — DM added to the sidebar")
        elif kind == "update":
            _, agent_id, name, model, prompt = result
            self.db.update_agent(agent_id, name, model,
                                 prompt or WORKER_DEFAULT_PROMPT.format(name=name))
        elif kind == "delete":
            self.db.delete_agent(result[1])
            self._refresh_chats()
            current = self.db.get_chat(self.chat_id) if self.chat_id else None
            if current is None:
                chats = self.db.list_chats(self.session_id)
                if chats:
                    self._open_chat(chats[0]["id"])
                else:
                    self.chat_id = None
                    self._clear_feed()
            self.notify("Agent deleted")
        self._refresh_chats()

    # -- group chats -------------------------------------------------------

    def _new_group(self) -> None:
        agents = self.db.list_agents(self.session_id)
        if not agents:
            self.notify("Create an agent first (Ctrl+N)", severity="warning")
            return
        self.push_screen(GroupModal(agents), self._on_group_result)

    def _on_group_result(self, result) -> None:
        if not result:
            return
        _, title, agent_ids = result
        chat_id = self.db.create_group_chat(self.session_id, title, agent_ids)
        self._refresh_chats()
        self._open_chat(chat_id)

    # -- sending -----------------------------------------------------------

    @on(Composer.Submitted)
    def _send(self, event: Composer.Submitted) -> None:
        text = (event.value or "").strip()
        if not text:
            return
        if self.chat_id is None:
            self.notify("Open a session first (Ctrl+S)", severity="warning")
            return
        if self._handle_slash(text):
            return
        self.db.append_message(self.chat_id, "user", None, text)
        feed = self._feed()
        view = MessageView("You", classes="me")
        view.set_body(text)
        feed.mount(view)
        feed.scroll_end(animate=False)
        self._respond()

    def _handle_slash(self, text: str) -> bool:
        """Run a command like /agents, /group, /sessions. Returns True if handled."""
        if not text.startswith("/"):
            return False
        command = text[1:].strip()
        if command == "agents":
            self.action_agents()
        elif command == "group":
            self._new_group()
        elif command == "sessions":
            self.action_sessions()
        else:
            self._render_notice(
                f"Unknown command /{command} — try /agents, /group, /sessions"
            )
        return True

    def _route_responders(self, chat, user_text: str) -> list:
        """Pick who answers: DM → its agent; group → @mentions, else the manager."""
        if chat["kind"] == "dm":
            agent = self.db.chat_agent(chat["id"])
            return [agent] if agent is not None else []
        members = self.db.agents_in_chat(chat["id"])
        if not members:
            return []
        by_name = {a["name"].lower(): a for a in members}
        responders: list = []
        for handle in re.findall(r"@([A-Za-z0-9_-]+)", user_text):
            agent = by_name.get(handle.lower())
            if agent is not None and agent not in responders:
                responders.append(agent)
        if responders:
            return responders
        manager = next((a for a in members if a["kind"] == "manager"), None)
        return [manager or members[0]]

    def _respond(self) -> None:
        chat = self.db.get_chat(self.chat_id)
        user_text = self.db.list_messages(self.chat_id)[-1]["content"]
        responders = self._route_responders(chat, user_text)
        if not responders:
            self._render_notice("Nobody to answer here — mention @name in a group")
            return
        chat_id = chat["id"]
        self.run_worker(
            self._respond_worker(chat_id, responders),
            group="turn",
            exclusive=True,
            name="turn",
        )

    def _turn_history(self, chat_id: int) -> list[dict[str, str]]:
        history: list[dict[str, str]] = []
        for message in self.db.list_messages(chat_id):
            if message["is_tool_round"] or not message["content"]:
                continue
            if message["delivered"]:
                history.append({
                    "role": "user",
                    "content": (f"message from {message['agent_name']}:"
                                f" {message['content']}"),
                })
                continue
            role = "user" if message["author"] == "user" else "assistant"
            history.append({"role": role, "content": message["content"]})
        return history

    async def _respond_worker(self, chat_id: int, responders: list) -> None:
        try:
            client = self._require_client()
        except ApiError as error:
            self._render_notice(str(error))
            return
        session = self.db.get_session(self.session_id)
        chat = self.db.get_chat(chat_id)
        try:
            executor = ToolExecutor(session["cwd"])
        except ToolError as error:
            self._render_notice(f"Workspace problem: {error}")
            return
        # One relay per user turn: initial responders plus queued agent→agent
        # messages, capped to keep chains from running away.
        relay = {"queue": asyncio.Queue(), "hops_left": 12, "turns_left": 20}
        for agent in responders:
            if agent is None or not self._claim_turn(relay):
                if agent is None:
                    continue
                break
            await self._agent_respond(
                client, chat_id, agent, executor, chat["perm_mode"], relay
            )
        while not relay["queue"].empty():
            if relay["hops_left"] <= 0 or relay["turns_left"] <= 0:
                break
            delivery = relay["queue"].get_nowait()
            relay["hops_left"] -= 1
            target = self.db.get_chat(delivery["chat_id"])
            if not self._claim_turn(relay):
                break
            await self._agent_respond(
                client, delivery["chat_id"], delivery["recipient"], executor,
                target["perm_mode"], relay,
            )
        if not relay["queue"].empty():
            self._render_notice(
                "Agent-to-agent relay budget exhausted — remaining messages"
                " are dropped"
            )

    @staticmethod
    def _claim_turn(relay: dict) -> bool:
        if relay["turns_left"] <= 0:
            return False
        relay["turns_left"] -= 1
        return True

    def _handle_manager_tool(self, sender, tool: str, arguments: dict,
                             relay: dict) -> str:
        if tool == "create_agent":
            return self._spawn_agent(arguments)
        if tool == "create_group":
            return self._spawn_group(sender, arguments)
        if tool == "assign_task":
            return self._assign_task(sender, arguments, relay)
        return f"ERROR: unknown manager tool {tool}"

    def _spawn_agent(self, arguments: dict) -> str:
        raw = str(arguments.get("name") or "")
        name = re.sub(r"[^A-Za-z0-9_-]", "", raw).strip("-_")[:24]
        if not name:
            return "ERROR: a name of letters, digits, _ or - is required"
        renamed = ""
        if self.db.agent_by_name(self.session_id, name):
            base, suffix = name, 2
            while self.db.agent_by_name(self.session_id, f"{base}{suffix}"):
                suffix += 1
            name = f"{base}{suffix}"
            renamed = " (name taken, renamed)"
        model = str(arguments.get("model") or "").strip() \
            or self.config.worker_model
        prompt = WORKER_DEFAULT_PROMPT.format(name=name)
        instructions = str(arguments.get("instructions") or "").strip()
        if instructions:
            prompt += "\n\n" + instructions
        agent_id = self.db.create_agent(self.session_id, name, model, prompt)
        self.db.get_or_create_dm(self.session_id, agent_id, name)
        self._refresh_chats()
        return f"created agent {name} (model {model}) with a DM chat{renamed}"

    def _spawn_group(self, sender, arguments: dict) -> str:
        title = str(arguments.get("title") or "").strip()
        raw = arguments.get("agents")
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except json.JSONDecodeError:
                raw = [p.strip() for p in raw.split(",") if p.strip()]
        member_ids: list[int] = [sender["id"]]
        missing: list[str] = []
        seen = set()
        for item in raw if isinstance(raw, list) else []:
            row = self.db.agent_by_name(self.session_id, str(item).strip())
            if row is None:
                missing.append(str(item))
            elif row["id"] not in seen and row["id"] != sender["id"]:
                seen.add(row["id"])
                member_ids.append(row["id"])
        if not title or len(member_ids) < 2:
            return (f"ERROR: title plus at least one valid teammate required;"
                    f" unknown names: {missing}")
        chat_id = self.db.create_group_chat(self.session_id, title, member_ids)
        self._refresh_chats()
        return f"created group {title!r} with {len(member_ids)} member(s)"

    def _assign_task(self, sender, arguments: dict, relay: dict) -> str:
        to_name = str(arguments.get("to_agent") or "").strip()
        task = str(arguments.get("task") or "").strip()
        if not to_name or not task:
            return "ERROR: to_agent and task are required"
        if relay["hops_left"] <= 0:
            return "ERROR: agent-to-agent message budget exhausted"
        recipient = self.db.agent_by_name(self.session_id, to_name)
        if recipient is None:
            names = ", ".join(a["name"] for a in self.db.list_agents(self.session_id))
            return f"ERROR: no teammate named {to_name!r}; teammates: {names}"
        if recipient["id"] == sender["id"]:
            return "ERROR: assign tasks to a teammate, not yourself"
        chat_id = self.db.get_or_create_dm(
            self.session_id, recipient["id"], recipient["name"]
        )
        text = f"[task from {sender['name']}] {task}"
        self.db.append_message(chat_id, "agent", sender["id"], text, delivered=1)
        self._render_delivered_view(chat_id, sender, text)
        relay["queue"].put_nowait({"chat_id": chat_id, "recipient": recipient})
        return f"assigned a task to {recipient['name']} in their direct chat"

    def _render_delivered_view(self, chat_id: int, sender, text: str) -> None:
        if self.chat_id != chat_id:
            return
        feed = self._feed()
        view = MessageView(sender["name"], model=sender["model"], classes="agent")
        view.set_body(text)
        feed.mount(view)
        feed.scroll_end(animate=False)

    def _handle_send_message(self, sender, chat_id: int, arguments: dict,
                             relay: dict) -> str:
        to_name = str(arguments.get("to_agent", "")).strip()
        text = str(arguments.get("text", "")).strip()
        private = bool(arguments.get("private"))
        if not to_name or not text:
            return "ERROR: to_agent and text are required"
        if relay["hops_left"] <= 0:
            return "ERROR: agent-to-agent message budget exhausted"
        recipient = self.db.agent_by_name(self.session_id, to_name)
        if recipient is None:
            names = ", ".join(a["name"] for a in self.db.list_agents(self.session_id))
            return f"ERROR: no teammate named {to_name!r}; teammates: {names}"
        if recipient["id"] == sender["id"]:
            return "ERROR: pick a teammate, not yourself"
        current = self.db.get_chat(chat_id)
        if not private and current and current["kind"] == "group":
            target_id = chat_id
            place = f"group {current['title']}"
        else:
            target_id = self.db.get_or_create_dm(
                self.session_id, recipient["id"], recipient["name"]
            )
            place = f"private chat with {recipient['name']}"
        self.db.append_message(target_id, "agent", sender["id"], text, delivered=1)
        self._render_delivered_view(target_id, sender, text)
        relay["queue"].put_nowait(
            {"chat_id": target_id, "recipient": recipient}
        )
        return f"delivered to {recipient['name']} in {place}"

    async def _agent_respond(self, client: OllamaClient, chat_id: int, agent,
                             executor: ToolExecutor, perm_mode: str,
                             relay: dict) -> None:
        """One full responding turn for one agent, with tool rounds."""
        state: dict = {"stream": None, "msg_id": None}

        def ensure_stream() -> StreamingMessage:
            if state["stream"] is None:
                stream = StreamingMessage(agent["name"], model=agent["model"])
                feed = self._feed()
                feed.mount(stream)
                feed.scroll_end(animate=False)
                state["stream"] = stream
                self._open_stream = stream
            return state["stream"]

        def write_event(event: dict) -> None:
            if event["type"] == "round_end":
                if state["stream"] is not None:
                    state["stream"].finalize(event["content"], event["thinking"])
                state["stream"] = None
                state["msg_id"] = self.db.append_message(
                    chat_id, "agent", agent["id"], event["content"],
                    event["thinking"],
                    tool_calls=json.dumps(event["tool_calls"]),
                    is_tool_round=1,
                )
                return
            ensure_stream().write_event(event)

        async def on_tool_call(call: dict) -> str:
            name = call["function"]["name"]
            arguments = call["function"]["arguments"]
            if state["msg_id"] is None:
                state["msg_id"] = self.db.append_message(
                    chat_id, "agent", agent["id"], "", "",
                    tool_calls=json.dumps([call]), is_tool_round=1,
                )
            message_id = state["msg_id"]

            if agent["kind"] == "manager" and name in (
                "create_agent", "create_group", "assign_task"
            ):
                result = self._handle_manager_tool(agent, name, arguments, relay)
                self.db.insert_tool_call(
                    message_id, name, json.dumps(arguments, ensure_ascii=False),
                    "done", result,
                )
                return result

            if name == "send_message":
                result = self._handle_send_message(agent, chat_id, arguments, relay)
                self.db.insert_tool_call(
                    message_id, name, json.dumps(arguments, ensure_ascii=False),
                    "done", result,
                )
                return result

            decision = self.permissions.decide(chat_id, perm_mode, name)
            if decision == ASK:
                choice = await self._await_approval(agent["name"], name, arguments)
                if choice == "always":
                    self.permissions.grant(chat_id, name)
                    decision = ALLOW
                elif choice == "deny":
                    decision = DENY
            if decision == DENY:
                status, result = "denied", "user denied this tool call"
            else:
                try:
                    result = await executor.execute(name, arguments)
                    status = "done"
                except ToolError as error:
                    result, status = f"ERROR: {error}", "failed"

            feed = self._feed()
            card = ToolCard(name, arguments)
            card.set_result(status, result, arguments)
            feed.mount(card)
            feed.scroll_end(animate=False)
            self.db.insert_tool_call(
                message_id, name, json.dumps(arguments, ensure_ascii=False),
                status, result,
            )
            return result

        workspace = self.db.get_session(self.session_id)["cwd"]
        others = [
            a for a in self.db.list_agents(self.session_id)
            if a["id"] != agent["id"]
        ]
        teammates = ", ".join(a["name"] for a in others) or "none yet"
        system_prompt = (
            agent["system_prompt"] + "\n\n"
            + TOOL_INSTRUCTIONS.format(workspace=workspace)
            + TEAM_INSTRUCTIONS.format(teammates=teammates)
        )
        tools_spec = AGENT_TOOLS_SPEC
        if agent["kind"] == "manager":
            system_prompt += MANAGER_INSTRUCTIONS
            if others:
                # A manager with a team coordinates only: no file tools, so it
                # cannot drift into doing or double-checking teammates' work.
                tools_spec = CHAT_TOOLS_SPEC + MANAGER_TOOLS_SPEC
            else:
                tools_spec = AGENT_TOOLS_SPEC + MANAGER_TOOLS_SPEC
        try:
            final = await run_agent_turn(
                client, agent["model"], system_prompt,
                self._turn_history(chat_id),
                tools_spec if agent["tools_enabled"] else None,
                write_event, on_tool_call, max_rounds=15,
            )
        except ApiError as error:
            ensure_stream().mark_error(str(error))
            self._open_stream = None
            return
        except Exception as error:  # surface bugs without killing the app
            ensure_stream().mark_error(f"unexpected: {error!r}")
            self._open_stream = None
            return

        content, thinking = final.get("content", ""), final.get("thinking", "")
        if final.get("budget_hit"):
            # The turn was cut off while the model kept requesting tools.
            ensure_stream().finalize(content, thinking)
            self.db.append_message(
                chat_id, "agent", agent["id"], content, thinking,
                tool_calls=json.dumps(final.get("tool_calls", [])),
                is_tool_round=1,
            )
            self._render_notice("Turn stopped at the tool-round budget")
            self._open_stream = None
            return
        if content or thinking:
            ensure_stream().finalize(content, thinking)
            self.db.append_message(chat_id, "agent", agent["id"], content, thinking)
        self._open_stream = None

    async def _await_approval(self, agent_name: str, tool: str,
                              arguments: dict) -> str:
        """Show the permission modal and wait for the user's choice."""
        future = asyncio.get_running_loop().create_future()

        def on_choice(choice: str | None) -> None:
            if not future.done():
                future.set_result(choice or "deny")

        self.push_screen(PermissionModal(agent_name, tool, arguments), on_choice)
        return await future

    def _abort_stream(self) -> None:
        if self._open_stream is not None:
            self.workers.cancel_group("turn")
            self._open_stream.mark_stopped()
            self._open_stream = None

    def _render_notice(self, text: str) -> None:
        notice = Static(text, classes="notice")
        feed = self._feed()
        feed.mount(notice)
        feed.scroll_end(animate=False)

    def action_feed_to_bottom(self) -> None:
        self._feed().scroll_end(animate=False)
