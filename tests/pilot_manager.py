"""Headless smoke test for M5: the manager builds a team autonomously.

Lead must spawn a Builder agent, assign the task through messaging, and
confirm once the Builder reports back. Uses the real Ollama Cloud API.
Run: .venv/bin/python tests/pilot_manager.py
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
            raise AssertionError(f"screen {kind.__name__} never appeared")

        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause(0.5)
            session_modal = await wait_screen(SessionModal)
            await pilot.click(session_modal.query_one("#btn-create"))
            await pilot.pause(0.5)

            conn = sqlite3.connect(db_path)
            conn.execute("UPDATE sessions SET cwd=?", (workspace,))
            conn.execute("UPDATE chats SET perm_mode='allow'")
            conn.commit()

            composer = app.query_one("#composer", Composer)
            composer.focus()
            await pilot.pause()
            text = (
                "Autonomously: spawn one agent named Builder, then give them a"
                " task to create team.txt containing exactly TEAM-OK and to"
                " report back to you with send_message (private). When they"
                " report, reply with just ORGANIZED. One exchange each way, no"
                " polite follow-ups. Tools are for Builder only — you just"
                " create, assign and wait for the report."
            )
            keys = [c if c != " " else "space" for c in text]
            await pilot.press(*keys)
            await pilot.press("enter")

            deadline = time.time() + 600
            finished = False
            while time.time() < deadline:
                if isinstance(app.screen, PermissionModal):
                    await pilot.click(app.screen.query_one("#btn-once"))
                    await pilot.pause(0.3)
                    continue
                rows = conn.execute(
                    "SELECT m.content FROM messages m JOIN chats c"
                    " ON c.id=m.chat_id WHERE c.kind='dm' AND m.author='agent'"
                    " AND m.delivered=0 AND m.is_tool_round=0"
                    " AND instr(lower(m.content),'organized')>0"
                ).fetchall()
                if rows:
                    finished = True
                    break
                await pilot.pause(1.0)
            if not finished:
                print("TIMEOUT DUMP — chats:",
                      conn.execute("SELECT kind, title FROM chats").fetchall())
                print("messages:", conn.execute(
                    "SELECT c.title, m.author, m.delivered, m.is_tool_round,"
                    " substr(m.content,1,60) FROM messages m JOIN chats c"
                    " ON c.id=m.chat_id ORDER BY m.id").fetchall())
                print("tool calls:", conn.execute(
                    "SELECT name, status, substr(result,1,40) FROM tool_calls"
                ).fetchall())
                print("modal:", isinstance(app.screen, PermissionModal))
            assert finished, "manager never produced ORGANIZED"

            agents = conn.execute(
                "SELECT name, kind FROM agents"
            ).fetchall()
            builders = [a for a in agents if a[0].lower().startswith("builder")]
            assert builders and builders[0][1] == "worker", agents
            work_calls = conn.execute(
                "SELECT name, status FROM tool_calls"
            ).fetchall()
            names = [w[0] for w in work_calls]
            assert "create_agent" in names, work_calls
            assert "assign_task" in names or "send_message" in names, work_calls
            assert (
                "assign_task" in names and "send_message" in names
            ), f"expected both delegation and report path: {work_calls}"
            with open(os.path.join(workspace, "team.txt")) as fh:
                written = fh.read()
            assert "TEAM-OK" in written, f"team.txt: {written!r}"
            print("LEAD:", rows[0][0][:80])
            print("MGR TOOL CALLS:", work_calls)
            erred = app.query(".errored")
            assert not erred, f"errored streams: {len(erred)}"

        app2 = MessyTeamApp(load_config())
        async with app2.run_test(size=(120, 45)) as pilot:
            await pilot.pause(1.0)
            chats = app2.db.list_chats(app2.session_id)
            assert len(chats) >= 2, f"chats after restart: {len(chats)}"
        print("PILOT MANAGER PASS")


if __name__ == "__main__":
    asyncio.run(main())
