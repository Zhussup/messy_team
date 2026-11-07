"""SQLite persistence: sessions, agents, chats (DM and group), messages."""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions(
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL,
  cwd TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS agents(
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  model TEXT NOT NULL,
  kind TEXT NOT NULL DEFAULT 'worker',
  system_prompt TEXT NOT NULL DEFAULT '',
  tools_enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(session_id, name)
);

CREATE TABLE IF NOT EXISTS chats(
  id INTEGER PRIMARY KEY,
  session_id INTEGER NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK(kind IN ('dm','group')),
  title TEXT NOT NULL,
  dm_agent_id INTEGER REFERENCES agents(id),
  perm_mode TEXT NOT NULL DEFAULT 'ask',
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  UNIQUE(session_id, dm_agent_id)
);

CREATE TABLE IF NOT EXISTS chat_members(
  chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  agent_id INTEGER NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
  PRIMARY KEY(chat_id, agent_id)
);

CREATE TABLE IF NOT EXISTS messages(
  id INTEGER PRIMARY KEY,
  chat_id INTEGER NOT NULL REFERENCES chats(id) ON DELETE CASCADE,
  author TEXT NOT NULL CHECK(author IN ('user','agent')),
  agent_id INTEGER REFERENCES agents(id),
  content TEXT NOT NULL DEFAULT '',
  thinking TEXT NOT NULL DEFAULT '',
  tool_calls TEXT,
  is_tool_round INTEGER NOT NULL DEFAULT 0,
  delivered INTEGER NOT NULL DEFAULT 0,
  reply_to INTEGER,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tool_calls(
  id INTEGER PRIMARY KEY,
  message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  args TEXT NOT NULL DEFAULT '{}',
  result TEXT,
  status TEXT NOT NULL DEFAULT 'pending',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class Database:
    """A thin sqlite wrapper. Single connection, WAL, used from the UI thread."""

    def __init__(self, path: Path | str) -> None:
        self.path = str(path)
        if path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            columns = lambda table: {  # noqa: E731
                row[1] for row in self._conn.execute(
                    f"PRAGMA table_info({table})"
                )
            }
            if version < 1:
                if "is_tool_round" not in columns("messages"):
                    self._conn.execute(
                        "ALTER TABLE messages"
                        " ADD COLUMN is_tool_round INTEGER NOT NULL DEFAULT 0"
                    )
                if "tools_enabled" not in columns("agents"):
                    self._conn.execute(
                        "ALTER TABLE agents"
                        " ADD COLUMN tools_enabled INTEGER NOT NULL DEFAULT 1"
                    )
            if version < 2:
                if "delivered" not in columns("messages"):
                    self._conn.execute(
                        "ALTER TABLE messages"
                        " ADD COLUMN delivered INTEGER NOT NULL DEFAULT 0"
                    )
            self._conn.execute("PRAGMA user_version = 2")
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # sessions -------------------------------------------------------------

    def list_sessions(self) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM sessions ORDER BY id DESC"
            ).fetchall()

    def create_session(self, title: str, cwd: str) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO sessions(title, cwd) VALUES(?, ?)", (title, cwd)
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_session(self, session_id: int) -> sqlite3.Row:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM sessions WHERE id=?", (session_id,)
            ).fetchone()

    # agents ---------------------------------------------------------------

    def create_agent(
        self,
        session_id: int,
        name: str,
        model: str,
        system_prompt: str,
        kind: str = "worker",
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO agents(session_id, name, model, kind, system_prompt)"
                " VALUES(?,?,?,?,?)",
                (session_id, name, model, kind, system_prompt),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_agents(self, session_id: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM agents WHERE session_id=? ORDER BY id", (session_id,)
            ).fetchall()

    def get_agent(self, agent_id: int) -> sqlite3.Row:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM agents WHERE id=?", (agent_id,)
            ).fetchone()

    def update_agent(
        self, agent_id: int, name: str, model: str, system_prompt: str
    ) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE agents SET name=?, model=?, system_prompt=? WHERE id=?",
                (name, model, system_prompt, agent_id),
            )
            self._conn.commit()

    def delete_agent(self, agent_id: int) -> None:
        """Remove the agent, its DM chats and its memberships."""
        with self._lock:
            self._conn.execute(
                "DELETE FROM chats WHERE session_id IN (SELECT session_id FROM agents"
                " WHERE id=?) AND dm_agent_id=?",
                (agent_id, agent_id),
            )
            self._conn.execute(
                "DELETE FROM chat_members WHERE agent_id=?", (agent_id,)
            )
            self._conn.execute("DELETE FROM agents WHERE id=?", (agent_id,))
            self._conn.commit()

    def session_manager(self, session_id: int) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM agents WHERE session_id=? AND kind='manager'"
                " ORDER BY id LIMIT 1",
                (session_id,),
            ).fetchone()

    def agent_by_name(self, session_id: int, name: str) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM agents WHERE session_id=? AND lower(name)=lower(?)"
                " LIMIT 1",
                (session_id, name),
            ).fetchone()

    def agents_in_chat(self, chat_id: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT a.* FROM chat_members m JOIN agents a ON a.id=m.agent_id"
                " WHERE m.chat_id=? ORDER BY a.id",
                (chat_id,),
            ).fetchall()

    # chats ----------------------------------------------------------------

    def create_chat(
        self,
        session_id: int,
        kind: str,
        title: str,
        dm_agent_id: int | None = None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO chats(session_id, kind, title, dm_agent_id)"
                " VALUES(?,?,?,?)",
                (session_id, kind, title, dm_agent_id),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def get_or_create_dm(self, session_id: int, agent_id: int, title: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM chats WHERE session_id=? AND dm_agent_id=?",
                (session_id, agent_id),
            ).fetchone()
            if row:
                return int(row["id"])
        return self.create_chat(session_id, "dm", title, dm_agent_id=agent_id)

    def create_group_chat(self, session_id: int, title: str, agent_ids: list[int]) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO chats(session_id, kind, title) VALUES(?, 'group', ?)",
                (session_id, title),
            )
            chat_id = int(cur.lastrowid)
            self._conn.executemany(
                "INSERT OR IGNORE INTO chat_members(chat_id, agent_id) VALUES(?,?)",
                [(chat_id, agent_id) for agent_id in agent_ids],
            )
            self._conn.commit()
            return chat_id

    def list_chats(self, session_id: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT c.*,"
                " (SELECT a.name FROM agents a WHERE a.id=c.dm_agent_id) AS agent_name"
                " FROM chats c WHERE c.session_id=? ORDER BY c.id",
                (session_id,),
            ).fetchall()

    def get_chat(self, chat_id: int) -> sqlite3.Row:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM chats WHERE id=?", (chat_id,)
            ).fetchone()

    def set_chat_perm_mode(self, chat_id: int, mode: str) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE chats SET perm_mode=? WHERE id=?", (mode, chat_id)
            )
            self._conn.commit()

    def chat_agent(self, chat_id: int) -> sqlite3.Row | None:
        """The agent a DM is addressed to, or the first member of a group."""
        with self._lock:
            row = self._conn.execute(
                "SELECT a.* FROM chats c JOIN agents a ON a.id=c.dm_agent_id"
                " WHERE c.id=?",
                (chat_id,),
            ).fetchone()
            if row:
                return row
            row = self._conn.execute(
                "SELECT a.* FROM chat_members m JOIN agents a ON a.id=m.agent_id"
                " WHERE m.chat_id=? ORDER BY a.id LIMIT 1",
                (chat_id,),
            ).fetchone()
            return row

    def add_chat_member(self, chat_id: int, agent_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT OR IGNORE INTO chat_members(chat_id, agent_id) VALUES(?,?)",
                (chat_id, agent_id),
            )
            self._conn.commit()

    # messages -------------------------------------------------------------

    def append_message(
        self,
        chat_id: int,
        author: str,
        agent_id: int | None,
        content: str,
        thinking: str = "",
        tool_calls: str | None = None,
        is_tool_round: int = 0,
        delivered: int = 0,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO messages(chat_id, author, agent_id, content, thinking,"
                " tool_calls, is_tool_round, delivered)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (chat_id, author, agent_id, content, thinking, tool_calls,
                 is_tool_round, delivered),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    # tool calls -----------------------------------------------------------

    def insert_tool_call(
        self,
        message_id: int,
        name: str,
        args_json: str,
        status: str,
        result: str | None,
    ) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO tool_calls(message_id, name, args, status, result)"
                " VALUES(?,?,?,?,?)",
                (message_id, name, args_json, status, result),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def list_tool_calls(self, message_id: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                "SELECT * FROM tool_calls WHERE message_id=? ORDER BY id",
                (message_id,),
            ).fetchall()

    _MESSAGE_JOIN = (
        "SELECT m.*, a.name AS agent_name, a.model AS agent_model"
        " FROM messages m LEFT JOIN agents a ON a.id=m.agent_id"
    )

    def list_messages(self, chat_id: int) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(
                f"{self._MESSAGE_JOIN} WHERE m.chat_id=? ORDER BY m.id", (chat_id,)
            ).fetchall()
