#!/usr/bin/env bash
set -euo pipefail

RUNTIME_VENV="${TRADEFLOW_VENV_PATH:-.venv}"

if ! command -v uv >/dev/null 2>&1; then
  echo "uv is required. Install it first: python3 -m pip install uv"
  exit 1
fi

uv venv "${RUNTIME_VENV}" --python 3.12
VIRTUAL_ENV="${RUNTIME_VENV}" uv sync --active --all-groups
echo "Environment ready: ${RUNTIME_VENV}"
