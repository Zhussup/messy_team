# Messy Team

A terminal messenger where your coding agents live.

Several sessions, each with its own workspace, roster of agents and chats.
You talk to agents in direct messages and group chats; agents talk to each
other; and the built-in **Lead** manager can spawn teammates, open groups and
hand out tasks on its own — like opencode, but as a team chat.

```
┌──────────────────────────────────────────────────────────┐
│  Chats              │  @ Builder  ·  tools: ask          │
│  ─────────────      │  ──────────────────────────────    │
│  · Lead             │  You: create notes.txt with a haiku│
│  · Builder          │  Builder thinks…                   │
│  # review-crew      │  ▸ write_file  notes.txt  ✓        │
│                     │  Builder: done — a haiku it is.    │
│                     │  ┌────────────────────────────┐    │
│                     │  │ > write here               │    │
│                     │  └────────────────────────────┘    │
└──────────────────────────────────────────────────────────┘
```

## How it works

- **Sessions** — each is a workspace directory plus its own SQLite history.
  Switch with `Ctrl+S`; everything (agents, chats, messages, tool calls)
  persists across restarts.
- **Agents** — LLM-backed coding agents (Ollama Cloud models) with file and
  shell tools: `read_file`, `write_file`, `edit_file`, `ls`, `glob`, `grep`,
  `bash`. Tool calls are shown as collapsible cards in the feed.
- **Chats** — direct chats (`@name`) and group chats (`#title`). In a group,
  `@mention` routes your message; without a mention the manager decides who
  answers.
- **Agent↔agent messaging** — agents call `send_message(to_agent, text,
  private)`. Public messages appear in the group; private ones are delivered
  to the teammate's direct chat, where you can read the whole exchange.
- **The manager** — every session starts with **Lead**, which has three extra
  tools: `create_agent`, `create_group`, `assign_task`. Ask it to organize
  work autonomously: it spawns teammates, delegates concrete tasks, and
  teammate reports arrive right back in its chat. A manager with a live team
  only coordinates messages — file work belongs to workers. Agent-to-agent
  chains run inside a per-turn budget, so they can't run away.
- **Permissions** — file writes and shell commands ask before running
  (Deny / Always in this chat / Once). Cycle the current chat's mode with
  `Ctrl+P`: ask → allow → read-only. Reads are always free.
- **Streaming** — replies and *thinking* stream into the feed; tool rounds,
  errors and rate-limit retries are handled mid-flight.

## Quick start

```bash
git clone https://github.com/Zhussup/messy_team.git
cd messy_team

# an Ollama Cloud key (create at https://ollama.com/settings/keys)
export OLLAMA_API_KEY="..."

./run.sh
```

That is the whole launch. `run.sh` creates a `.venv` on first run, installs
the dependencies into it and starts the TUI — no activation needed.

The manual equivalent, if you prefer not to use the script:

```bash
python3 -m venv .venv
.venv/bin/pip install .
.venv/bin/messy_team
```

Note: outside the `.venv` there is no `messy_team` command and system
`pip` is locked down by Debian (`externally-managed`), so always use
`.venv/bin/...` — or run `./run.sh` and let it handle that. After a
`git pull`, `./run.sh` reinstalls automatically when `pyproject.toml`
changed.

## Key bindings and commands

| Key / command | Action |
|---|---|
| `Ctrl+S` | open / create a session |
| `Ctrl+N` | agent manager (create, edit, delete) |
| `Ctrl+P` | cycle tool permission mode of the open chat |
| `Ctrl+L` | scroll the feed to the bottom |
| `/agents`, `/group` | agent manager, new group chat |
| `/model` | edit the current chat's agent (with a model picker) |
| `/balance` | remaining Ollama Cloud credits |
| `/sessions` | session switcher |

Enter sends, Shift+Enter breaks a line.

## Configuration

Optional `~/.config/messy-team/config.toml`:

```toml
api_base = "https://ollama.com/api"
api_key = "..."             # OLLAMA_API_KEY env var also works
manager_model = "glm-5.3"
worker_model = "glm-5.3-flash"
db_path = "~/.local/share/messy-team/messy.db"
```

History lives in a single SQLite database (`WAL` mode) under
`~/.local/share/messy-team/` by default. Override any location with
`MESSY_TEAM_CONFIG`, `MESSY_TEAM_DATA_DIR` or `MESSY_TEAM_DB`.

## Development

Pilot tests drive the real Textual app headless against the live API
(they spend credits and take several minutes each):

```bash
.venv/bin/python tests/pilot_smoke.py     # session, streaming reply, restart
.venv/bin/python tests/pilot_agents.py    # agents, DMs, groups, @routing
.venv/bin/python tests/pilot_tools.py     # tool loop, permissions, tool cards
.venv/bin/python tests/pilot_relay.py     # agent-to-agent relay chains
.venv/bin/python tests/pilot_manager.py   # autonomous manager: spawn + delegate
.venv/bin/python tests/pilot_polish.py    # /model picker, /balance
.venv/bin/python tests/pilot_crash.py kill   # then: ... check
```

## License

[MIT](LICENSE)
