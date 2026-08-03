#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROFILE_PATH="${PROJECT_DIR}/config/practice.env"
TARGET="${1:-langgraph}"

if [[ ! -f "${PROFILE_PATH}" ]]; then
  echo "Practice profile is missing: ${PROFILE_PATH}" >&2
  exit 1
fi

set -a
# shellcheck source=/dev/null
source "${PROFILE_PATH}"
set +a

cd "${PROJECT_DIR}"

case "${TARGET}" in
  langgraph)
    exec ./scripts/start_langgraph_dev.sh
    ;;
  chat)
    exec ./scripts/start_demo.sh
    ;;
  api)
    exec ./scripts/start_api.sh
    ;;
  ui)
    exec ./scripts/start_ui.sh
    ;;
  all)
    exec ./scripts/start_all_demo.sh
    ;;
  *)
    echo "Usage: $0 [all|langgraph|chat|api|ui]" >&2
    exit 2
    ;;
esac
