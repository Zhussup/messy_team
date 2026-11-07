"""Headless smoke test for M4: agent-to-agent relay.

Atlas (mentioned in a group) must delegate privately to Nova, Nova must do
the file work and report back privately, Atlas must finish in her own DM.
Uses the real Ollama Cloud API.
Run: .venv/bin/python tests/pilot_relay.py
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import time

from textual.widgets import Input

from messy_team.agent import WORKER_DEFAULT_PROMPT


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
        conn_holder = {}

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

            conn = conn_holder["conn"] = sqlite3.connect(db_path)
            conn.execute("UPDATE sessions SET cwd=?", (workspace,))
            conn.commit()

            # Two workers and a group chat, permission mode "allow".
            atlas_id = app.db.create_agent(
                app.session_id, "Atlas", app.config.worker_model,
                WORKER_DEFAULT_PROMPT.format(name="Atlas"),
            )
            nova_id = app.db.create_agent(
                app.session_id, "Nova", app.config.worker_model,
                WORKER_DEFAULT_PROMPT.format(name="Nova"),
            )
            group_id = app.db.create_group_chat(
                app.session_id, "delegation", [atlas_id, nova_id]
            )
            conn.execute("UPDATE chats SET perm_mode='allow'")
            conn.commit()
            app._refresh_chats()
            app._open_chat(group_id)

            composer = app.query_one("#composer", Composer)
            composer.focus()
            await pilot.pause()
            text = (
                "@Atlas Delegate privately to Nova: she must create relay.txt"
                " containing exactly RELAY-OK. You must NOT create any files or"
                " run any tools yourself — only message Nova with send_message"
                " (private=true). When she tells you it is done, reply with just"
                " COMPLETE in your own direct chat. Exchange at most one message"
                " each way — no polite follow-ups, no thank-you notes."
            )
            await pilot.press(*[c if c != " " else "space" for c in text])
            await pilot.press("enter")

            deadline = time.time() + 420
            finished = False
            while time.time() < deadline:
                if isinstance(app.screen, PermissionModal):
                    await pilot.click(app.screen.query_one("#btn-once"))
                    await pilot.pause(0.3)
                    continue
                rows = conn.execute(
                    "SELECT m.content, a.name FROM messages m JOIN agents a"
                    " ON a.id=m.agent_id JOIN chats c ON c.id=m.chat_id"
                    " WHERE c.kind='dm' AND m.author='agent' AND m.delivered=0"
                    " AND m.is_tool_round=0 AND instr(lower(m.content),'complet')>0"
                ).fetchall()
                if rows:
                    finished = True
                    break
                await pilot.pause(1.0)
            if not finished:
                print("TIMEOUT DUMP — messages:")
                for row in conn.execute(
                    "SELECT c.title, m.author, m.delivered, m.is_tool_round,"
                    " substr(m.content,1,60) FROM messages m"
                    " JOIN chats c ON c.id=m.chat_id ORDER BY m.id"
                ).fetchall():
                    print("   ", row)
                print("tool calls:", conn.execute(
                    "SELECT name, status, substr(result,1,40) FROM tool_calls"
                ).fetchall())
                print("modal on screen:", isinstance(app.screen, PermissionModal))
            assert finished, "relay chain never produced a COMPLETE confirmation"

            with open(os.path.join(workspace, "relay.txt")) as fh:
                written = fh.read()
            assert "RELAY-OK" in written, f"relay.txt: {written!r}"
            print(f"ATLAS DM CONFIRM: {rows[0][0][:80]!r}")

            sends = conn.execute(
                "SELECT tc.result FROM messages m JOIN tool_calls tc"
                " ON tc.message_id=m.id WHERE tc.name='send_message'"
                " AND tc.status='done'"
            ).fetchall()
            results = " | ".join(r[0] for r in sends)
            print("SEND MESSAGES:", results[:200])
            assert len(sends) >= 2, f"expected >=2 relays: {results!r}"

            dms = conn.execute(
                "SELECT c.title, COUNT(*) FROM chats c WHERE c.kind='dm'"
                " GROUP BY c.id"
            ).fetchall()
            print(f"AFTER RESTART PREP: {len(dms)} DM chats: {dms}")
            assert len(dms) >= 3, f"3 DMs expected (Lead, Nova, Atlas): {dms}"

            errored = app.query(".errored")
            assert not errored, f"errored streams: {len(errored)}"

        # Restart: all three DM chats survive.
        app2 = MessyTeamApp(load_config())
        async with app2.run_test(size=(120, 45)) as pilot:
            await pilot.pause(1.0)
            chats = app2.db.list_chats(app2.session_id)
            assert len(chats) >= 4, f"chats after restart: {len(chats)}"
        print("PILOT RELAY PASS")


if __name__ == "__main__":
    asyncio.run(main())
