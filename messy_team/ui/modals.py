"""Modal screens: the session switcher / creator."""

from __future__ import annotations

import os

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, ListItem, ListView, Static

# choice is None (cancelled) or ("open", session_id) or
# ("create", title, cwd).
Choice = None | tuple[str, int] | tuple[str, str, str]


class SessionModal(ModalScreen[Choice]):
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
                    for s in self._sessions:
                        item = ListItem(
                            Static(
                                f"{s['title']}  ·  {s['cwd']}", classes="chat-label"
                            )
                        )
                        item.session_id = s["id"]
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

    @property
    def _title_input(self) -> Input:
        return self.query_one("#inp-title", Input)

    @property
    def _cwd_input(self) -> Input:
        return self.query_one("#inp-cwd", Input)

    def _cwd(self) -> str:
        return os.path.expanduser(self._cwd_input.value.strip() or os.getcwd())

    def _create(self) -> None:
        cwd = self._cwd()
        if not os.path.isdir(cwd):
            self.notify(f"Workspace directory does not exist: {cwd}", severity="error")
            return
        title = self._title_input.value.strip() or "session"
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
