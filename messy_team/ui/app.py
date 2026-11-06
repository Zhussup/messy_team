"""The Messy Team app: sidebar with chats, feed, composer."""

from __future__ import annotations

import re
from dataclasses import dataclass

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, ScrollableContainer, Vertical
from textual.message import Message
from textual.widgets import Footer, Header, ListItem, ListView, Static, TextArea

from ..agent import (
    LEAD_DEFAULT_PROMPT,
    WORKER_DEFAULT_PROMPT,
    run_chat_turn,
)
from ..config import Config
from ..db import Database
from ..ollama import ApiError, OllamaClient
from .modals import AgentManagerModal, GroupModal, SessionModal
from .widgets import MessageView, StreamingMessage


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
        chat = self.db.get_chat(chat_id)
        icon = "@" if chat["kind"] == "dm" else "#"
        self.query_one("#chat-title", Static).update(f"{icon} {chat['title']}")
        feed = self._feed()
        feed.remove_children()
        for message in self.db.list_messages(chat_id):
            view = self._view_for_message(message)
            feed.mount(view)
        feed.scroll_end(animate=False)

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
            if not message["content"]:
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
        for agent in responders:
            if agent is None:
                continue
            stream = StreamingMessage(agent["name"], model=agent["model"])
            feed = self._feed()
            feed.mount(stream)
            feed.scroll_end(animate=False)
            self._open_stream = stream
            history = self._turn_history(chat_id)
            try:
                final = await run_chat_turn(
                    client, agent["model"], agent["system_prompt"], history,
                    stream.write_event,
                )
            except ApiError as error:
                stream.mark_error(str(error))
                self._open_stream = None
                break  # stop the chain, e.g. on rate limits
            except Exception as error:  # surface bugs without killing the app
                stream.mark_error(f"unexpected: {error!r}")
                self._open_stream = None
                continue
            stream.finalize(final["content"], final["thinking"])
            self.db.append_message(
                chat_id, "agent", agent["id"], final["content"],
                thinking=final["thinking"],
            )
            self._open_stream = None

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
