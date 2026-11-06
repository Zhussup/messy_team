"""Configuration and data-file locations.

Env overrides:
- MESSY_TEAM_CONFIG        path of the config file
- MESSY_TEAM_DATA_DIR      directory holding the database
- MESSY_TEAM_DB            full path of the SQLite database
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass
from pathlib import Path

DEFAULT_API_BASE = "https://ollama.com/api"
DEFAULT_MANAGER_MODEL = "glm-5.3"
DEFAULT_WORKER_MODEL = "glm-5.3-flash"


def config_path() -> Path:
    env = os.environ.get("MESSY_TEAM_CONFIG")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "messy-team" / "config.toml"


def data_dir() -> Path:
    env = os.environ.get("MESSY_TEAM_DATA_DIR")
    if env:
        return Path(env)
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "messy-team"


@dataclass(slots=True)
class Config:
    api_base: str
    api_key: str | None
    manager_model: str
    worker_model: str
    db_path: Path


def load_config() -> Config:
    path = config_path()
    data: dict = {}
    if path.is_file():
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    # The environment wins over the config file.
    api_key = os.environ.get("OLLAMA_API_KEY") or data.get("api_key")
    db_env = os.environ.get("MESSY_TEAM_DB")
    if db_env:
        db_path = Path(db_env)
    else:
        db_path = Path(data.get("db_path", data_dir() / "messy.db"))
    return Config(
        api_base=data.get("api_base", DEFAULT_API_BASE),
        api_key=api_key,
        manager_model=data.get("manager_model", DEFAULT_MANAGER_MODEL),
        worker_model=data.get("worker_model", DEFAULT_WORKER_MODEL),
        db_path=db_path,
    )
