"""Feed widgets: message views, streaming messages and tool cards."""

from __future__ import annotations

import json

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Collapsible, Markdown, Static

from ..agent import Event

_STATUS_SYMBOL = {"done": "✓", "failed": "✗", "denied": "⊘"}


def _safe_title(text: str) -> str:
    # Collapsible titles render through markup; keep brackets out.
    return text.replace("[", "(").replace("]", ")")


class MessageView(Vertical):
    """One rendered message in the feed: an author line and a markdown body."""

    def __init__(self, who: str, model: str | None = None, classes: str = "") -> None:
        super().__init__(classes=f"msg {classes}".strip())
        kind = "who-me" if "me" in classes.split() else "who-agent"
        label = who + (f" · {model}" if model else "")
        self.who = Static(label, classes=f"who {kind}")
        self.thinking_line = Static("✻ thinking…", classes="thinking")
        self.thinking_line.display = False
        self.body = Markdown("", classes="body")

    def compose(self) -> ComposeResult:
        yield self.who
        yield self.thinking_line
        yield self.body

    def set_body(self, text: str) -> None:
        self.body.update(text or "*— no content —*")

    def set_thinking(self, live: bool, text: str = "") -> None:
        self.thinking_line.display = True
        self.thinking_line.set_class(live, "live")
        if text:
            self.thinking_line.update(text)


class StreamingMessage(MessageView):
    """A message that accumulates deltas while the agent is replying."""

    FLUSH_INTERVAL = 0.08

    def __init__(self, who: str, model: str | None = None) -> None:
        super().__init__(who, model=model, classes="streaming")
        self._parts: list[str] = []
        self._flush_scheduled = False

    def write_event(self, event: Event) -> None:
        if event["type"] == "content":
            self._parts.append(event["text"])
            self._schedule_flush()
        elif event["type"] == "thinking":
            self.set_thinking(live=True)

    def _schedule_flush(self) -> None:
        if self._flush_scheduled:
            return
        self._flush_scheduled = True
        self.set_timer(self.FLUSH_INTERVAL, self._flush)

    def _flush(self) -> None:
        self._flush_scheduled = False
        self.set_body("".join(self._parts))
        self._scroll_feed()

    def finalize(self, content: str, thinking: str) -> None:
        self.remove_class("streaming")
        if thinking:
            self.set_thinking(live=False, text="✻ thought for a bit")
        self.set_body(content)
        self._scroll_feed()

    def mark_error(self, text: str) -> None:
        self.remove_class("streaming")
        self.add_class("errored")
        self.set_body(f"**error:** {text}")
        self._scroll_feed()

    def mark_stopped(self) -> None:
        self.remove_class("streaming")
        self.who.update(f"{self.who.renderable}  · stopped")

    def _scroll_feed(self) -> None:
        parent = self.parent
        if parent is not None:
            parent.scroll_end(animate=False)


class ToolCard(Collapsible):
    """A finished tool call: header shows the outcome, body args and result."""

    RESULT_CAP = 2_000

    def __init__(self, tool_name: str, arguments: dict, status: str = "pending",
                 result: str | None = None) -> None:
        self._detail = Static("pending…", classes="tool-detail")
        super().__init__(
            self._detail,
            title=f"⚙ {tool_name}",
            collapsed=True,
            classes="tool-card",
        )
        self._tool = tool_name
        self.set_result(status, result, arguments)

    @classmethod
    def from_record(cls, record) -> "ToolCard":
        try:
            arguments = json.loads(record["args"])
            if not isinstance(arguments, dict):
                arguments = {"args": arguments}
        except (json.JSONDecodeError, TypeError):
            arguments = {"args": record["args"]}
        return cls(record["name"], arguments, record["status"], record["result"])

    def set_result(self, status: str, result: str | None,
                   arguments: dict | None = None) -> None:
        summary = " ".join(
            str(v).replace("\n", " ") for v in (arguments or {}).values()
        )[:44]
        self.title = f"{_STATUS_SYMBOL.get(status, '?')} ⚙ {self._tool} {summary}".rstrip()
        self._detail.update(Text(
            f"args: {json.dumps(arguments or {}, ensure_ascii=False, indent=2)}\n\n"
            f"result: {(result or '—')[: self.RESULT_CAP]}"
        ))
