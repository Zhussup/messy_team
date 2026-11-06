"""Headless smoke test: sessions modal, sending a message, streamed reply.

Uses the real Ollama Cloud API (needs OLLAMA_API_KEY in the environment) and a
throwaway database. Run: .venv/bin/python tests/pilot_smoke.py
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import tempfile
import time


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        os.environ["MESSY_TEAM_DB"] = os.path.join(tmp, "test.db")
        os.environ["MESSY_TEAM_CONFIG"] = os.path.join(tmp, "no-config.toml")
        # Import after env is set so load_config() sees the overrides.
        from messy_team.config import load_config
        from messy_team.ui.app import Composer, MessyTeamApp

        app = MessyTeamApp(load_config())
        db_path = os.environ["MESSY_TEAM_DB"]
        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause(0.5)
            # Fresh database: the session modal must be open without Cancel.
            from messy_team.ui.modals import SessionModal

            deadline = time.time() + 20
            while not isinstance(app.screen, SessionModal) and time.time() < deadline:
                await pilot.pause(0.2)
            assert isinstance(app.screen, SessionModal), f"no modal: {app.screen!r}"
            create_button = app.screen.query_one("#btn-create")
            await pilot.click(create_button)
            await pilot.pause(1.0)

            composer = app.query_one("#composer", Composer)
            assert composer.display, "composer must be visible after session setup"
            composer.focus()
            await pilot.pause()
            text = "Reply with exactly one word in caps: PONG"
            keys: list[str] = []
            for ch in text:
                keys.append(ch if ch != " " else "space")
            await pilot.press(*keys)
            await pilot.press("enter")
            await pilot.pause(0.5)

            conn = sqlite3.connect(db_path)
            deadline = time.time() + 240
            reply = ""
            while time.time() < deadline:
                rows = conn.execute(
                    "SELECT m.content FROM messages m JOIN chats c ON c.id=m.chat_id"
                    " WHERE c.kind='dm' AND m.author='agent'"
                ).fetchall()
                if rows:
                    reply = rows[-1][0] or ""
                    break
                await pilot.pause(1.0)
            print("REPLY:", reply.replace("\n", " ")[:200])
            assert "PONG" in reply.upper(), f"no PONG in reply: {reply!r}"

        # Restart on the same database: history must load, no modal.
        app2 = MessyTeamApp(load_config())
        async with app2.run_test(size=(120, 45)) as pilot:
            await pilot.pause(1.0)
            msgs = app2.query(".msg")
            print(f"AFTER RESTART: {len(msgs)} messages in feed")
            assert len(msgs) >= 2, "history must be restored after restart"
            # No session modal on restart: an existing session is picked up.
            assert not isinstance(app2.screen, SessionModal), (
                "restart must reuse the existing session"
            )
        print("PILOT SMOKE PASS")


if __name__ == "__main__":
    asyncio.run(main())
