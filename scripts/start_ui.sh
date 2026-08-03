#!/usr/bin/env bash
set -euo pipefail

export PYDANTIC_DISABLE_PLUGINS="${PYDANTIC_DISABLE_PLUGINS:-1}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_VENV="${TRADEFLOW_VENV_PATH:-.venv}"
export PYTHONPATH="${PROJECT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

cd "${PROJECT_DIR}"

exec "${RUNTIME_VENV}/bin/streamlit" run app/ui/streamlit_app.py \
  --server.address 127.0.0.1 \
  --server.port "${TRADEFLOW_UI_PORT:-8501}" \
  --server.headless true
