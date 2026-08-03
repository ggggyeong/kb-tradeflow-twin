#!/usr/bin/env bash
set -euo pipefail

export PYDANTIC_DISABLE_PLUGINS="${PYDANTIC_DISABLE_PLUGINS:-1}"

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUNTIME_VENV="${TRADEFLOW_VENV_PATH:-${PROJECT_DIR}/.venv}"
API_PORT="${TRADEFLOW_API_PORT:-8000}"
export PYTHONPATH="${PROJECT_DIR}${PYTHONPATH:+:${PYTHONPATH}}"

cd "${PROJECT_DIR}"

"${RUNTIME_VENV}/bin/uvicorn" app.api.main:app \
  --host 127.0.0.1 \
  --port "${API_PORT}" &
API_PID=$!

cleanup() {
  kill "${API_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

TRADEFLOW_API_URL="http://127.0.0.1:${API_PORT}" \
  "${RUNTIME_VENV}/bin/streamlit" run app/ui/streamlit_app.py \
  --server.address 127.0.0.1 \
  --server.port "${TRADEFLOW_UI_PORT:-8501}" \
  --server.headless true
