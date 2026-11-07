"""Headless smoke test for M6 polish: /model picker, /balance, persistence.

/model must open the Lead agent modal with a cloud-model picker; picking an
entry fills the field, saving persists it. /balance must print a notice.
Run: .venv/bin/python tests/pilot_polish.py
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
        from messy_team.config import load_config
        from messy_team.ui.app import Composer, MessyTeamApp
        from messy_team.ui.modals import AgentModal, SessionModal

        app = MessyTeamApp(load_config())
        db_path = os.environ["MESSY_TEAM_DB"]
        picked: list[str] = []

        async def send(text: str) -> None:
            composer = app.query_one("#composer", Composer)
            composer.focus()
            await pilot.pause()
            await pilot.press(*[c if c != " " else "space" for c in text])
            await pilot.press("enter")
            await pilot.pause(0.3)

        async def wait(predicate, timeout=20.0):
            deadline = time.time() + timeout
            while time.time() < deadline:
                await pilot.pause(0.2)
                result = predicate()
                if result:
                    return result
            raise AssertionError("wait timed out")

        async with app.run_test(size=(120, 45)) as pilot:
            await pilot.pause(0.5)
            deadline = time.time() + 10
            while not isinstance(app.screen, SessionModal):
                await pilot.pause(0.2)
                assert time.time() < deadline, "no session modal"
            await pilot.click(app.screen.query_one("#btn-create"))
            await pilot.pause(0.5)

            conn = sqlite3.connect(db_path)

            # /model opens the Lead agent editor with the cloud model picker.
            await send("/model")
            modal = await wait(lambda: app.screen
                               if isinstance(app.screen, AgentModal) else None)
            picker = await wait(
                lambda: modal.query_one("#model-picker") if modal.query("#model-picker")
                else None
            )
            assert picker.option_count > 0, "model picker is empty"
            target = picker.get_option_at_index(2)
            picker.focus()
            await pilot.press("down", "down")
            await pilot.press("enter")
            await pilot.pause(0.3)
            field = modal.query_one("#inp-model")
            assert field.value == str(target.prompt), \
                f"picker did not fill the field: {field.value!r}"
            lead_id = app.db.session_manager(app.session_id)["id"]
            await pilot.click(modal.query_one("#btn-save"))
            await pilot.pause(0.3)
            row = app.db.get_agent(lead_id)
            assert row["model"] == str(target.prompt), \
                f"model not saved: {row['model']!r}"
            picked.append(row["model"])
            print("PICKED MODEL:", picked[0])

            # /balance writes a notice into the feed.
            await send("/balance")
            notice_text = await wait(lambda: next(
                (str(n.render()) for n in app.query(".notice")
                 if "balance" in str(n.render()).lower() or "$" in str(n.render())),
                None))
            print("BALANCE NOTICE:", notice_text.strip()[:100])

        app2 = MessyTeamApp(load_config())
        async with app2.run_test(size=(120, 45)) as pilot:
            await pilot.pause(1.0)
            row = app2.db.get_agent(
                app2.db.session_manager(app2.session_id)["id"]
            )
            assert row["model"] == picked[0], \
                f"model lost on restart: {row['model']!r}"
        print("PILOT POLISH PASS")


if __name__ == "__main__":
    asyncio.run(main())
