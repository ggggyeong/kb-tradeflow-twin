#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LANGGRAPH_PORT="${TRADEFLOW_LANGGRAPH_PORT:-2024}"
LANGGRAPH_URL="http://127.0.0.1:${LANGGRAPH_PORT}"
export TRADEFLOW_LANGGRAPH_URL="${TRADEFLOW_LANGGRAPH_URL:-${LANGGRAPH_URL}}"

cd "${PROJECT_DIR}"

./scripts/start_langgraph_dev.sh &
LANGGRAPH_PID=$!

cleanup() {
  kill "${LANGGRAPH_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

READY=0
for _ in $(seq 1 60); do
  if curl --fail --silent --max-time 2 "${LANGGRAPH_URL}/docs" >/dev/null; then
    READY=1
    break
  fi
  if ! kill -0 "${LANGGRAPH_PID}" 2>/dev/null; then
    echo "LangGraph Dev exited before becoming ready." >&2
    exit 1
  fi
  sleep 1
done

if [[ "${READY}" != "1" ]]; then
  echo "LangGraph Dev did not become ready at ${LANGGRAPH_URL}." >&2
  exit 1
fi

exec ./scripts/start_demo.sh
