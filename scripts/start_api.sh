#!/usr/bin/env bash
set -euo pipefail

export PYDANTIC_DISABLE_PLUGINS="${PYDANTIC_DISABLE_PLUGINS:-1}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_VENV="${TRADEFLOW_VENV_PATH:-${PROJECT_DIR}/.venv}"
export PYTHONPATH="${PROJECT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

cd "${PROJECT_DIR}"
exec "${RUNTIME_VENV}/bin/uvicorn" app.api.main:app \
  --host 127.0.0.1 \
  --port "${TRADEFLOW_API_PORT:-8000}"
