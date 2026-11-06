"""Modal screens: session switcher, agent management, group creation."""

from __future__ import annotations

import os

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Input,
    ListItem,
    ListView,
    SelectionList,
    Static,
    TextArea,
)
from textual.widgets.selection_list import Selection

from .. import suggested_names

# Session choice: None (cancelled), ("open", session_id) or
# ("create", title, cwd).
SessionChoice = None | tuple[str, int] | tuple[str, str, str]

# Agent edit result: None, ("create", name, model, prompt),
# ("update", agent_id, name, model, prompt) or ("delete", agent_id).
AgentEditResult = None | tuple


class SessionModal(ModalScreen[SessionChoice]):
    """Open an existing session or create a new one."""

    CSS = """
    SessionModal { align: center middle; }
    #session-dialog {
        width: 64; height: auto; max-height: 80%;
        border: round $accent; background: $surface; padding: 1 2;
    }
    #session-dialog .title { text-style: bold; margin-bottom: 1; }
    #session-list { height: auto; max-height: 10; margin-bottom: 1;
                    border: round $surface-lighten-2; }
    #session-dialog Input { margin-bottom: 1; }
    #session-dialog .btns { height: auto; margin-top: 1; align: right middle; }
    #session-dialog Button { margin-left: 2; }
    """

    BINDINGS = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, sessions: list, can_cancel: bool = True) -> None:
        super().__init__()
        self._sessions = sessions
        self._can_cancel = can_cancel
        cwd = os.getcwd()
        self._default_title = os.path.basename(cwd.rstrip("/")) or "session"
        self._default_cwd = cwd

    def compose(self) -> ComposeResult:
        with Vertical(id="session-dialog"):
            yield Static("Sessions", classes="title")
            if self._sessions:
                with ListView(id="session-list"):
                    for session in self._sessions:
                        item = ListItem(
                            Static(
                                f"{session['title']}  ·  {session['cwd']}",
                                classes="chat-label",
                            )
                        )
                        item.session_id = session["id"]
                        yield item
            yield Static("New session", classes="title")
            yield Input(
                id="inp-title", placeholder="Session name", value=self._default_title
            )
            yield Input(
                id="inp-cwd", placeholder="Workspace directory", value=self._default_cwd
            )
            with Horizontal(classes="btns"):
                if self._can_cancel:
                    yield Button("Cancel", id="btn-cancel")
                yield Button("Create", id="btn-create", variant="primary")

    def action_cancel(self) -> None:
        if self._can_cancel:
            self.dismiss(None)
        else:
            self.notify("Pick or create a session first", severity="warning")

    def _create(self) -> None:
        cwd = os.path.expanduser(self.query_one("#inp-cwd", Input).value.strip()
                                 or os.getcwd())
        if not os.path.isdir(cwd):
            self.notify(f"Workspace directory does not exist: {cwd}", severity="error")
            return
        title = self.query_one("#inp-title", Input).value.strip() or "session"
        self.dismiss(("create", title, cwd))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-create":
            self._create()
        elif event.button.id == "btn-cancel":
            self.dismiss(None)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self._create()

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        session_id = getattr(event.item, "session_id", None)
        if session_id is not None:
            self.dismiss(("open", session_id))


class AgentModal(ModalScreen[AgentEditResult]):
    """Create or edit an agent: name, model, system prompt."""

    CSS = """
    AgentModal { align: center middle; }
    #agent-dialog {
        width: 70; height: auto; max-height: 85%;
        border: round $primary; background: $surface; padding: 1 2;
    }
    #agent-dialog .title { text-style: bold; margin-bottom: 1; }
    #agent-dialog Input { margin-bottom: 1; }
    #agent-dialog TextArea { height: 6; margin-bottom: 1;
                             border: round $surface-lighten-2; }
    #agent-dialog .hint { color: $text-muted; margin-bottom: 1; }
    #agent-dialog .btns { height: auto; align: right middle; }
    #agent-dialog Button { margin-left: 2; }
    """

    def __init__(
        self, agent=None, suggestion: str | None = None, default_model: str = ""
    ) -> None:
        super().__init__()
        self._agent = agent
        self._suggestion = suggestion
        self._default_model = default_model

    def compose(self) -> ComposeResult:
        editing = self._agent is not None
        name = self._agent["name"] if editing else (self._suggestion or "")
        model = self._agent["model"] if editing else self._default_model
        prompt = (self._agent["system_prompt"] if editing else "") or ""
        with Vertical(id="agent-dialog"):
            yield Static("Edit agent" if editing else "New agent", classes="title")
            yield Input(id="inp-name", placeholder="Name", value=name)
            yield Input(id="inp-model", placeholder="Model (e.g. glm-5.3-flash)",
                        value=model)
            yield TextArea(prompt, id="inp-prompt", soft_wrap=True)
            yield Static("Empty prompt keeps the default worker prompt",
                         classes="hint")
            with Horizontal(classes="btns"):
                yield Button("Cancel", id="btn-cancel")
                if editing:
                    yield Button("Delete", id="btn-delete", variant="error")
                    yield Button("Save", id="btn-save", variant="primary")
                else:
                    yield Button("Create", id="btn-create", variant="primary")

    def _fields(self) -> tuple[str, str, str]:
        name = self.query_one("#inp-name", Input).value.strip()
        model = self.query_one("#inp-model", Input).value.strip()
        prompt = self.query_one("#inp-prompt", TextArea).text
        return name, model, prompt

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
        elif event.button.id in ("btn-create", "btn-save"):
            name, model, prompt = self._fields()
            if not name or not model:
                self.notify("Name and model are required", severity="error")
                return
            if event.button.id == "btn-create":
                self.dismiss(("create", name, model, prompt))
            else:
                self.dismiss(("update", self._agent["id"], name, model, prompt))
        elif event.button.id == "btn-delete":
            self.dismiss(("delete", self._agent["id"]))


class AgentManagerModal(ModalScreen[bool]):
    """List the session's agents: select to edit, create new ones, close."""

    CSS = """
    AgentManagerModal { align: center middle; }
    #agents-dialog {
        width: 64; height: auto; max-height: 82%;
        border: round $accent; background: $surface; padding: 1 2;
    }
    #agents-dialog .title { text-style: bold; margin-bottom: 1; }
    #agents-list { height: auto; max-height: 12; border: round $surface-lighten-2;
                   margin-bottom: 1; }
    #agents-dialog .btns { height: auto; align: right middle; }
    #agents-dialog Button { margin-left: 2; }
    """

    def __init__(self, agents: list, on_change=None) -> None:
        super().__init__()
        self._agents = agents
        self._on_change = on_change

    def compose(self) -> ComposeResult:
        with Vertical(id="agents-dialog"):
            yield Static(f"Agents ({len(self._agents)})", classes="title")
            with ListView(id="agents-list"):
                for agent in self._agents:
                    yield self._item(agent)
            with Horizontal(classes="btns"):
                yield Button("New agent", id="btn-new-agent", variant="primary")
                yield Button("Close", id="btn-close")

    @staticmethod
    def _item(agent) -> ListItem:
        tag = " · manager" if agent["kind"] == "manager" else ""
        item = ListItem(
            Static(f"· {agent['name']} · {agent['model']}{tag}", classes="chat-label")
        )
        item.agent_id = agent["id"]
        return item

    def _push_agent_modal(self, agent=None) -> None:
        taken = [a["name"] for a in self._agents]
        suggestion = None if agent else (suggested_names.suggest(taken, 1) or [None])[0]
        self.app.push_screen(
            AgentModal(
                agent=agent,
                suggestion=suggestion,
                default_model=self.app.config.worker_model,
            ),
            self._on_agent_modal_closed,
        )

    def _on_agent_modal_closed(self, result: AgentEditResult) -> None:
        if result is None:
            return
        if self._on_change is not None:
            self._on_change(result)
        # Refresh the visible list in place.
        self._agents = self.app.db.list_agents(self.app.session_id)
        agents_list = self.query_one("#agents-list", ListView)
        agents_list.clear()
        for agent in self._agents:
            agents_list.append(self._item(agent))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-new-agent":
            self._push_agent_modal()
        elif event.button.id == "btn-close":
            self.dismiss(True)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        agent_id = getattr(event.item, "agent_id", None)
        if agent_id is not None:
            agent = self.app.db.get_agent(agent_id)
            if agent is not None:
                self._push_agent_modal(agent)


# Group result: None or ("create", title, agent_ids).
GroupResult = None | tuple[str, str, list[int]]


class GroupModal(ModalScreen[GroupResult]):
    """Create a group chat: a title plus a multi-select member list."""

    CSS = """
    GroupModal { align: center middle; }
    #group-dialog {
        width: 64; height: auto; max-height: 82%;
        border: round $accent; background: $surface; padding: 1 2;
    }
    #group-dialog .title { text-style: bold; margin-bottom: 1; }
    #group-dialog Input { margin-bottom: 1; }
    #group-agents { height: auto; max-height: 10; border: round $surface-lighten-2;
                    margin-bottom: 1; }
    #group-dialog .hint { color: $text-muted; margin-bottom: 1; }
    #group-dialog .btns { height: auto; align: right middle; }
    #group-dialog Button { margin-left: 2; }
    """

    def __init__(self, agents: list) -> None:
        super().__init__()
        self._agents = agents

    def compose(self) -> ComposeResult:
        with Vertical(id="group-dialog"):
            yield Static("New group chat", classes="title")
            yield Input(id="inp-group-title", placeholder="Group name")
            yield Static("Agents to add (Space toggles, Enter confirms):",
                         classes="hint")
            yield SelectionList[int](
                *[
                    Selection(f"{a['name']} · {a['model']}", a["id"], False)
                    for a in self._agents
                ],
                id="group-agents",
            )
            with Horizontal(classes="btns"):
                yield Button("Cancel", id="btn-cancel")
                yield Button("Create", id="btn-create", variant="primary")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn-cancel":
            self.dismiss(None)
        elif event.button.id == "btn-create":
            title = self.query_one("#inp-group-title", Input).value.strip()
            picked = list(self.query_one("#group-agents", SelectionList).selected)
            if not title or not picked:
                self.notify("Name and at least one agent are required",
                            severity="error")
                return
            self.dismiss(("create", title, picked))
