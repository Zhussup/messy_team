#!/usr/bin/env bash
# Messy Team launcher: bootstraps .venv and starts the TUI, no activation.
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -x .venv/bin/messy_team ] || [ pyproject.toml -nt .venv/bin/messy_team ]; then
  if [ ! -x .venv/bin/python ]; then
    echo "First run: creating .venv and installing dependencies..."
    python3 -m venv .venv
    .venv/bin/pip install --quiet --upgrade pip
  else
    echo "pyproject.toml changed: reinstalling..."
  fi
  .venv/bin/pip install --quiet .
fi

exec .venv/bin/messy_team "$@"
