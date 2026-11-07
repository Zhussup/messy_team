"""Final milestone check: a hard kill mid-stream must not corrupt anything.

Two stages share one database file:
  tests/pilot_crash.py kill   — create a session, start a reply, os._exit()
  tests/pilot_crash.py check  — reopen: session/chats/messages/perm mode
                                survived, and a new reply still streams
Run: .venv/bin/python tests/pilot_crash.py kill; .venv/bin/python tests/pilot_crash.py check
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile
import time

DB_ENV = "MESSY_TEAM_DB"


async def main(stage: str) -> None:
    tmp = sys.argv[2] if len(sys.argv) > 2 else ".messy-crash-check"
    os.makedirs(tmp, exist_ok=True)
    os.environ[DB_ENV] = os.path.join(tmp, "crash.db")
    os.environ["MESSY_TEAM_CONFIG"] = os.path.join(tmp, "no-config.toml")
    from messy_team.config import load_config
    from messy_team.ui.app import Composer, MessyTeamApp
    from messy_team.ui.modals import SessionModal

    async def send(pilot, app, text: str) -> None:
        composer = app.query_one("#composer", Composer)
        composer.focus()
        await pilot.pause()
        await pilot.press(*[c if c != " " else "space" for c in text])
        await pilot.press("enter")
        await pilot.pause(0.3)

    if stage == "kill":
        app = MessyTeamApp(load_config())
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause(0.5)
            deadline = time.time() + 10
            while not isinstance(app.screen, SessionModal):
                await pilot.pause(0.2)
                assert time.time() < deadline, "no session modal"
            await pilot.click(app.screen.query_one("#btn-create"))
            await pilot.pause(0.5)
            session_id = app.session_id
            # Persist "allow" for this chat: permissions must survive kills.
            app.db.set_chat_perm_mode(app.chat_id, "allow")
            await send(pilot, app, "Reply with the word STREAMING and a long"
                                    " story about turtles")
            # Wait until a streaming reply is actually on screen, then die.
            deadline = time.time() + 120
            while time.time() < deadline:
                streams = app.query(".streaming")
                if streams:
                    print("STREAM LIVE — killing hard now")
                    break
                await pilot.pause(0.2)
            else:
                print("WARNING: reply finished before the kill; still valid")
            print(f"SESSION_ID={session_id}")
            with open(os.path.join(tmp, "session_id.txt"), "w") as fh:
                fh.write(str(session_id))
            sys.stdout.flush()
            os._exit(127)  # no cleanup on purpose

    assert stage == "check", f"unknown stage {stage!r}"
    app = MessyTeamApp(load_config())
    async with app.run_test(size=(120, 45)) as pilot:
        await pilot.pause(1.0)
        assert not isinstance(app.screen, SessionModal), \
            "restart showed the modal: session was lost"
        expected_id = int(
            open(os.path.join(tmp, "session_id.txt")).read().strip()
        )
        assert app.session_id == expected_id, \
            f"session mismatch: {app.session_id} != {expected_id}"
        conn = sqlite3.connect(os.environ[DB_ENV])
        chats = conn.execute("SELECT id, kind, perm_mode FROM chats").fetchall()
        assert chats and chats[0][2] == "allow", f"perm mode lost: {chats}"
        user_rows = conn.execute(
            "SELECT content FROM messages WHERE author='user'").fetchall()
        assert user_rows, "user message lost"
        print(f"AFTER KILL: chats={len(chats)} user messages={len(user_rows)}")

        composer = app.query_one("#composer", Composer)
        composer.focus()
        await pilot.press(*[
            c if c != " " else "space" for c in "New prompt: reply with PONG!"
        ])
        await pilot.press("enter")
        deadline = time.time() + 240
        while time.time() < deadline:
            rows = conn.execute(
                "SELECT m.content FROM messages m"
                " WHERE m.author='agent' AND m.is_tool_round=0"
                " AND instr(lower(m.content),'pong')>0"
            ).fetchall()
            if rows:
                print("POST-KILL REPLY:", rows[0][0][:60])
                break
            await pilot.pause(1.0)
        else:
            raise AssertionError("app dead after kill: no new reply")
    print("PILOT CRASH PASS")


if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else ""
    asyncio.run(main(stage))
