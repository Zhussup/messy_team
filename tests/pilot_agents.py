"""Headless smoke test for M2: agent CRUD modal, group chat, @mention routing.

Uses the real Ollama Cloud API (needs OLLAMA_API_KEY).
Run: .venv/bin/python tests/pilot_agents.py
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import time

from textual.widgets import Input


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MESSY_TEAM_DB"] = os.path.join(tmp, "test.db")
        os.environ["MESSY_TEAM_CONFIG"] = os.path.join(tmp, "no-config.toml")
        from messy_team.config import load_config
        from messy_team.ui.app import Composer, MessyTeamApp
        from messy_team.ui.modals import AgentManagerModal, AgentModal, SessionModal

        app = MessyTeamApp(load_config())
        db_path = os.environ["MESSY_TEAM_DB"]

        async def wait_screen(kind, timeout=10.0):
            deadline = time.time() + timeout
            while time.time() < deadline:
                await pilot.pause(0.2)
                if isinstance(app.screen, kind):
                    return app.screen
            raise AssertionError(f"screen {kind.__name__} never appeared;"
                                 f" current: {app.screen!r}")

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
            # 1. Create session through the modal.
            session_modal = await wait_screen(SessionModal)
            await pilot.click(session_modal.query_one("#btn-create"))
            await pilot.pause(0.5)

            conn = sqlite3.connect(db_path)
            agents = conn.execute("SELECT * FROM agents").fetchall()
            assert len(agents) == 1 and agents[0][2] == "Lead", agents
            lead_id = agents[0][0]

            # 2. Create an agent via Ctrl+N -> manager modal -> new agent.
            await pilot.press("ctrl+n")
            manager = await wait_screen(AgentManagerModal)
            await pilot.click(manager.query_one("#btn-new-agent"))
            agent_modal = await wait_screen(AgentModal)
            suggested_name = agent_modal.query_one("#inp-name", Input).value
            assert suggested_name, "a suggested name must be prefilled"
            await pilot.click(agent_modal.query_one("#btn-create"))
            await pilot.pause(0.5)
            agents = conn.execute("SELECT id, name FROM agents").fetchall()
            assert len(agents) == 2, agents
            nova = next(a for a in agents if a[1] == suggested_name)
            dms = conn.execute(
                "SELECT COUNT(*) FROM chats WHERE kind='dm'"
            ).fetchone()[0]
            assert dms == 2, f"each agent gets a DM: {dms}"
            await pilot.click(app.screen.query_one("#btn-close"))
            await pilot.pause(0.3)

            # 3. Group chat: @mention routes to the mentioned agent only.
            chat_id = app.db.create_group_chat(
                app.session_id, "crew", [lead_id, nova[0]]
            )
            app._refresh_chats()
            app._open_chat(chat_id)
            await send(f"@{suggested_name} Reply with exactly GROUPOK in caps")
            deadline = time.time() + 240
            replies = []
            while time.time() < deadline:
                replies = conn.execute(
                    "SELECT a.name, m.content FROM messages m JOIN agents a"
                    " ON a.id=m.agent_id WHERE m.chat_id=? AND m.author='agent'",
                    (chat_id,),
                ).fetchall()
                if replies:
                    break
                await pilot.pause(1.0)
            assert replies, "no reply in the group"
            assert replies[0][0] == suggested_name, f"wrong responder: {replies[0][0]}"
            assert "GROUPOK" in replies[0][1].upper(), replies[0][1]
            assert len(replies) == 1, f"mention must route to one agent: {replies}"
            print(f"GROUP REPLY: {suggested_name}: {replies[0][1][:100]!r}")

            # 4. Slash commands.
            await send("/no-such-cmd")
            await pilot.pause(0.3)
            await send("/agents")
            await wait_screen(AgentManagerModal)
            await pilot.click(app.screen.query_one("#btn-close"))
            await pilot.pause(0.3)
        print("PILOT AGENTS PASS")


if __name__ == "__main__":
    asyncio.run(main())
