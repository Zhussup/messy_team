"""Workspace tools for coding agents.

Every tool is sandboxed to the session workspace: paths are resolved against
the workspace root and must never escape it. Tools return plain strings; a
controlled failure yields "ERROR: ..." instead of an exception.
"""

from __future__ import annotations

import asyncio
import fnmatch
import glob as globmod
import json
import os
import re
from pathlib import Path
from typing import Any

READ_CAP = 60_000
LIST_CAP = 500
GLOB_CAP = 200
GREP_MATCH_CAP = 100
GREP_FILE_CAP = 2_000
BASH_TIMEOUT = 120.0
BASH_CAP = 20_000
SKIP_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".messy"}


class ToolError(Exception):
    pass


def _fn(name: str, description: str, params: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": name, "description": description, "parameters": params},
    }


TOOLS_SPEC = [
    _fn("read_file", "Read a text file from the workspace. Returns its content.",
        {"type": "object",
         "properties": {
             "path": {"type": "string", "description": "Workspace-relative path"},
             "offset": {"type": "integer",
                        "description": "1-based line to start from"},
             "limit": {"type": "integer", "description": "Max lines to return"},
         },
         "required": ["path"]}),
    _fn("write_file",
        "Create or overwrite a text file with the given content.",
        {"type": "object",
         "properties": {
             "path": {"type": "string", "description": "Workspace-relative path"},
             "content": {"type": "string", "description": "Full file content"},
         },
         "required": ["path", "content"]}),
    _fn("edit_file",
        "Replace old_string with new_string in a file. old_string must match"
        " exactly once unless replace_all is true.",
        {"type": "object",
         "properties": {
             "path": {"type": "string", "description": "Workspace-relative path"},
             "old_string": {"type": "string"},
             "new_string": {"type": "string"},
             "replace_all": {"type": "boolean"},
         },
         "required": ["path", "old_string", "new_string"]}),
    _fn("ls", "List a workspace directory (default: the root).",
        {"type": "object",
         "properties": {
             "path": {"type": "string", "description": "Directory, optional"},
         }}),
    _fn("glob", "List files matching a glob pattern, e.g. src/**/*.py.",
        {"type": "object",
         "properties": {
             "pattern": {"type": "string"},
         },
         "required": ["pattern"]}),
    _fn("grep", "Regex search across workspace text files."
        " Returns file:line:text matches.",
        {"type": "object",
         "properties": {
             "pattern": {"type": "string", "description": "Python regex"},
             "glob": {"type": "string",
                      "description": "Only files matching this glob, optional"},
         },
         "required": ["pattern"]}),
    _fn("bash", "Run a shell command inside the workspace. 120s timeout.",
        {"type": "object",
         "properties": {
             "command": {"type": "string"},
         },
         "required": ["command"]}),
]


class ToolExecutor:
    def __init__(self, workspace: str) -> None:
        self.root = Path(workspace).resolve()
        if not self.root.is_dir():
            raise ToolError(f"workspace does not exist: {self.root}")
        self._handlers = {
            "read_file": self._read_file,
            "write_file": self._write_file,
            "edit_file": self._edit_file,
            "ls": self._ls,
            "glob": self._glob,
            "grep": self._grep,
        }

    def _resolve(self, rel: str) -> Path:
        raw = (rel or "").strip()
        if not raw:
            raw = "."
        path = Path(raw)
        resolved = (path if path.is_absolute()
                    else self.root / path).resolve()
        if resolved != self.root and self.root not in resolved.parents:
            raise ToolError(f"path escapes the workspace: {raw}")
        return resolved

    async def execute(self, name: str, arguments: dict[str, Any]) -> str:
        try:
            if name == "bash":
                return await self._bash(arguments.get("command", ""))
            handler = self._handlers.get(name)
            if handler is None:
                return f"ERROR: unknown tool {name}"
            return handler(arguments or {})
        except ToolError as error:
            return f"ERROR: {error}"
        except Exception as error:  # never crash the agent loop
            return f"ERROR: internal: {error!r}"

    # handlers ---------------------------------------------------------------

    def _read_file(self, args: dict[str, Any]) -> str:
        path = self._resolve(args.get("path", ""))
        if not path.is_file():
            raise ToolError(f"not a file: {args.get('path')}")
        text = path.read_text(errors="replace")
        lines = text.splitlines()
        offset = max(1, int(args.get("offset") or 1))
        limit = int(args.get("limit") or len(lines) - offset + 1)
        chosen = lines[offset - 1:offset - 1 + max(0, limit)]
        out = "\n".join(chosen)
        if len(out) > READ_CAP:
            out = out[:READ_CAP] + "\n…(truncated)"
        return out or "(file is empty)"

    def _write_file(self, args: dict[str, Any]) -> str:
        path = self._resolve(args.get("path", ""))
        content = args.get("content")
        if not isinstance(content, str):
            raise ToolError("content must be a string")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return f"wrote {len(content)} bytes to {path.name}"

    def _edit_file(self, args: dict[str, Any]) -> str:
        path = self._resolve(args.get("path", ""))
        old, new = args.get("old_string"), args.get("new_string")
        if not isinstance(old, str) or not isinstance(new, str):
            raise ToolError("old_string and new_string must be strings")
        if not path.is_file():
            raise ToolError(f"not a file: {args.get('path')}")
        text = path.read_text(errors="replace")
        count = text.count(old)
        if count == 0:
            raise ToolError("old_string not found in file")
        if count > 1 and not args.get("replace_all"):
            raise ToolError(
                f"old_string matches {count} times; pass replace_all: true"
            )
        text = text.replace(old, new) if args.get("replace_all") \
            else text.replace(old, new, 1)
        path.write_text(text, encoding="utf-8")
        return f"edited {path.name}"

    def _ls(self, args: dict[str, Any]) -> str:
        path = self._resolve(args.get("path") or "")
        if not path.is_dir():
            raise ToolError(f"not a directory: {args.get('path') or '.'}")
        entries = []
        for child in sorted(path.iterdir()):
            if child.name in SKIP_DIRS:
                continue
            entries.append(child.name + ("/" if child.is_dir() else ""))
            if len(entries) >= LIST_CAP:
                entries.append("…(more)")
                break
        return "\n".join(entries) or "(empty)"

    def _glob(self, args: dict[str, Any]) -> str:
        pattern = args.get("pattern", "*")
        matches = sorted({
            str(Path(m).relative_to(self.root))
            for m in globmod.glob(str(self.root / pattern), recursive=True)
        })[:GLOB_CAP]
        if len(matches) == GLOB_CAP:
            matches.append("…(capped)")
        return "\n".join(matches) or "(no matches)"

    def _grep(self, args: dict[str, Any]) -> str:
        pattern = args.get("pattern", "")
        glob_filter = args.get("glob")
        try:
            regex = re.compile(pattern)
        except re.error as error:
            raise ToolError(f"bad regex: {error}") from error
        hits: list[str] = []
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
            for filename in filenames:
                full = Path(dirpath) / filename
                rel = full.relative_to(self.root).as_posix()
                if glob_filter and not fnmatch.fnmatch(rel, glob_filter):
                    continue
                try:
                    with open(full, "rb") as fh:
                        head = fh.read(1024)
                    if b"\x00" in head:
                        continue
                    with open(full, encoding="utf-8", errors="replace") as fh:
                        for lineno, line in enumerate(fh, 1):
                            if regex.search(line):
                                hits.append(f"{rel}:{lineno}:{line.rstrip()}")
                                if len(hits) >= GREP_MATCH_CAP:
                                    hits.append("…(capped)")
                                    return "\n".join(hits)
                            if lineno > GREP_FILE_CAP:
                                break
                except (OSError, UnicodeError):
                    continue
        return "\n".join(hits) or "(no matches)"

    async def _bash(self, command: str) -> str:
        if not command.strip():
            raise ToolError("empty command")
        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=self.root,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=os.environ.copy(),
            )
        except (OSError, NotImplementedError) as error:
            raise ToolError(f"cannot spawn shell: {error}") from error
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), BASH_TIMEOUT)
        except asyncio.TimeoutError:
            proc.kill()
            return f"ERROR: command timed out after {BASH_TIMEOUT:.0f}s"
        output = stdout.decode(errors="replace") if stdout else ""
        if len(output) > BASH_CAP:
            output = output[:BASH_CAP] + "\n…(truncated)"
        return f"exit {proc.returncode}\n{output}"
