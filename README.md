# Messy Team

A messenger-style terminal app where coding agents live in sessions, DMs and
group chats — like opencode, but the whole thing looks like a chat client.

Built with [Textual](https://github.com/Textualize/textual), models served by
[Ollama Cloud](https://ollama.com) (OpenAI-ecosystem cloud LLMs via the native
Ollama API).

## Status

Work in progress.

- **M1 (current)** — app skeleton: sessions, DM chats with one agent, streaming
  replies, SQLite persistence.
- **M2+** — agent management, group chats, coding tools with permission
  prompts, agent-to-agent messaging, a coordinator agent that spawns and
  manages teammates, autonomous teams.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e .
export OLLAMA_API_KEY=...   # create a key at https://ollama.com/settings/keys
.venv/bin/messy_team
```

Configuration lives in `~/.config/messy-team/config.toml` (optional):

```toml
api_base = "https://ollama.com/api"
manager_model = "glm-5.3"
worker_model = "glm-5.3-flash"
```

`OLLAMA_API_KEY` from the environment wins over `api_key` in the config file.

## Layout

- **Sidebar** — chats of the current session.
- **Feed** — message history of the open chat.
- **Composer** — `Enter` sends, `Shift+Enter` makes a newline.

`Ctrl+S` switches sessions, `Ctrl+Q` quits. Data is stored in
`~/.local/share/messy-team/messy.db`.

## License

MIT — see [LICENSE](LICENSE).
