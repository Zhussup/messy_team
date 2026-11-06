"""Headless smoke test for M3: tools with permission approval.

An agent must read a notes file, create a new file (approved once by the
user in a modal) and reply. Uses the real Ollama Cloud API.
Run: .venv/bin/python tests/pilot_tools.py
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import time


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        workspace = os.path.join(tmp, "ws")
        os.makedirs(workspace)
        with open(os.path.join(workspace, "notes.txt"), "w") as fh:
            fh.write("the magic output file name is result.txt\n")

        os.environ["MESSY_TEAM_DB"] = os.path.join(tmp, "test.db")
        os.environ["MESSY_TEAM_CONFIG"] = os.path.join(tmp, "no-config.toml")
        from messy_team.config import load_config
        from messy_team.ui.app import Composer, MessyTeamApp
        from messy_team.ui.modals import PermissionModal, SessionModal

        app = MessyTeamApp(load_config())
        db_path = os.environ["MESSY_TEAM_DB"]

        async def wait_screen(kind, timeout=10.0):
            deadline = time.time() + timeout
            while time.time() < deadline:
                await pilot.pause(0.2)
                if isinstance(app.screen, kind):
                    return app.screen
            raise AssertionError(
                f"screen {kind.__name__} never appeared; current: {app.screen!r}"
            )

        async def send(text: str) -> None:
            composer = app.query_one("#composer", Composer)
            composer.focus()
            await pilot.pause()
            keys = [ch if ch != " " else "space" for ch in text]
            await pilot.press(*keys)
            await pilot.press("enter")
            await pilot.pause(0.3)

        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause(0.5)
            session_modal = await wait_screen(SessionModal)
            await pilot.click(session_modal.query_one("#btn-create"))
            await pilot.pause(0.5)

            conn = sqlite3.connect(db_path)
            # Point the session workspace at the temp workspace.
            conn.execute("UPDATE sessions SET cwd=?", (workspace,))
            conn.commit()

            # Permission-mode cycle (Ctrl+P behavior), direct call.
            app.action_tools_mode()   # ask -> allow
            mode = conn.execute("SELECT perm_mode FROM chats LIMIT 1").fetchone()[0]
            assert mode == "allow", mode
            app.action_tools_mode()   # allow -> readonly
            mode = conn.execute("SELECT perm_mode FROM chats LIMIT 1").fetchone()[0]
            assert mode == "readonly", mode
            app.action_tools_mode()   # readonly -> ask
            mode = conn.execute("SELECT perm_mode FROM chats LIMIT 1").fetchone()[0]
            assert mode == "ask", mode

            await send(
                "Use tools: read notes.txt, then create result.txt with"
                " exactly the one line RESULT-OK, then reply with just DONE."
            )

            # Approve tool calls until the agent finishes.
            approved = 0
            deadline = time.time() + 400
            done = False
            while time.time() < deadline:
                if isinstance(app.screen, PermissionModal):
                    await pilot.click(app.screen.query_one("#btn-once"))
                    approved += 1
                    await pilot.pause(0.3)
                    continue
                if os.path.exists(os.path.join(workspace, "result.txt")):
                    rows = conn.execute(
                        "SELECT content FROM messages m JOIN chats c"
                        " ON c.id=m.chat_id WHERE m.author='agent'"
                        " AND m.is_tool_round=0 ORDER BY m.id DESC LIMIT 1"
                    ).fetchall()
                    if rows:
                        done = True
                        break
                await pilot.pause(0.5)
            assert done, "agent turn never finished"
            print(f"APPROVALS: {approved}")

            with open(os.path.join(workspace, "result.txt")) as fh:
                written = fh.read()
            assert "RESULT-OK" in written, f"file content: {written!r}"
            calls = conn.execute(
                "SELECT name, status FROM tool_calls ORDER BY id"
            ).fetchall()
            print("TOOL CALLS:", calls)
            assert any(c[0] == "write_file" and c[1] == "done" for c in calls), calls
            assert approved >= 1, "the write_file call must have asked for approval"

        # Restart: tool cards and history must be restored.
        app2 = MessyTeamApp(load_config())
        async with app2.run_test(size=(120, 45)) as pilot:
            await pilot.pause(1.0)
            cards = app2.query(".tool-card")
            print(f"AFTER RESTART: {len(cards)} tool cards, "
                  f"{len(app2.query('.msg'))} messages")
            assert len(cards) >= 2, "tool cards must be restored on restart"
        print("PILOT TOOLS PASS")


if __name__ == "__main__":
    asyncio.run(main())
