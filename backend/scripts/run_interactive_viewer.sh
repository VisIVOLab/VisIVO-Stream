#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

cd "${BACKEND_DIR}"

if [[ -f ".env" ]]; then
  while IFS= read -r line || [[ -n "${line}" ]]; do
    [[ -z "${line}" ]] && continue
    [[ "${line}" =~ ^[[:space:]]*# ]] && continue
    if [[ "${line}" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      export "${line}"
    fi
  done < ".env"
fi

PVPYTHON_BIN="${PVPYTHON_BIN:-pvpython}"
TRAME_HOST="${TRAME_HOST:-127.0.0.1}"
TRAME_PORT="${TRAME_PORT:-8081}"
API_BASE_URL="${API_BASE_URL:-http://127.0.0.1:8000/api/v1}"
TRAME_DATASET_ID="${TRAME_DATASET_ID:-}"

echo "Starting VisIVO-Stream interactive viewer on http://${TRAME_HOST}:${TRAME_PORT}"
cmd=(
  "${PVPYTHON_BIN}" -m app.interactive.trame_viewer
  --api-base-url "${API_BASE_URL}"
  --host "${TRAME_HOST}"
  --port "${TRAME_PORT}"
)

if [[ -n "${TRAME_DATASET_ID}" ]]; then
  cmd+=(--dataset-id "${TRAME_DATASET_ID}")
fi

cmd+=("$@")
exec "${cmd[@]}"
