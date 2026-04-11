#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

DEFAULT_PYTHON="$REPO_DIR/.venv/bin/python"
if [[ -x "$DEFAULT_PYTHON" ]]; then
  PYTHON_BIN="${PYTHON_BIN:-$DEFAULT_PYTHON}"
else
  PYTHON_BIN="${PYTHON_BIN:-python3}"
fi

if [[ $# -gt 0 && "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
  TRADE_DATE="$1"
  shift
else
  TRADE_DATE="${TRADE_DATE:-$("$PYTHON_BIN" - <<'PY'
from datetime import datetime, timedelta, timezone
print((datetime.now(timezone.utc).date() - timedelta(days=1)).isoformat())
PY
)}"
fi

DB_PATH="${DB_PATH:-$REPO_DIR/signals.sqlite3}"
EXPORT_ROOT="${EXPORT_ROOT:-$REPO_DIR/reconciliation-daily/$TRADE_DATE}"
CYCLES="${CYCLES:-96}"

mkdir -p "$EXPORT_ROOT"
cd "$REPO_DIR"

exec "$PYTHON_BIN" backtest.py \
  --cycles "$CYCLES" \
  --end-date "$TRADE_DATE" \
  --export-dir "$EXPORT_ROOT" \
  --reconcile-live-db "$DB_PATH" \
  --reconcile-date "$TRADE_DATE" \
  --log-reconciliation-db "$DB_PATH" \
  "$@"
