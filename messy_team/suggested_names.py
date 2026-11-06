"""Default name pool for new agents. The user may keep or change them."""

from __future__ import annotations

POOL = [
    "Nova",
    "Atlas",
    "Sage",
    "Bolt",
    "Iris",
    "Onyx",
    "Echo",
    "Juno",
    "Pixel",
    "Comet",
    "Fable",
    "Quill",
    "Rex",
    "Orbit",
    "Wren",
]


def suggest(taken: list[str] | None = None, count: int = 3) -> list[str]:
    """Return unused suggestions, keeping the pool order."""
    taken_lower = {t.lower() for t in (taken or [])}
    return [n for n in POOL if n.lower() not in taken_lower][:count]
