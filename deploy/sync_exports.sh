#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
REPORT_SCRIPT="${MODEL050426_REPORT_SCRIPT:-${REPO_ROOT}/report.py}"

VPS_HOST="${MODEL050426_VPS_HOST:-root@204.168.202.167}"
REMOTE_ROOT="${MODEL050426_REMOTE_ROOT:-/opt/MODEL05042026}"
REMOTE_DB_PATH="${MODEL050426_REMOTE_DB_PATH:-${REMOTE_ROOT}/data/signals.sqlite3}"
REMOTE_SERVICE_NAME="${MODEL050426_REMOTE_SERVICE_NAME:-model050426}"
REMOTE_TMP_DIR="${MODEL050426_REMOTE_TMP_DIR:-/tmp}"
LOCAL_SYNC_ROOT="${MODEL050426_LOCAL_SYNC_ROOT:-${HOME}/MODEL050426-sync}"
LOCAL_DB_PATH="${MODEL050426_LOCAL_DB_PATH:-${LOCAL_SYNC_ROOT}/db/signals.sqlite3}"
LOCAL_EXPORT_DIR="${MODEL050426_LOCAL_EXPORT_DIR:-${LOCAL_SYNC_ROOT}/exports/latest}"
LOCAL_REPORT_PATH="${MODEL050426_LOCAL_REPORT_PATH:-${LOCAL_SYNC_ROOT}/reports/latest.txt}"
LOCAL_STATUS_PATH="${MODEL050426_LOCAL_STATUS_PATH:-${LOCAL_SYNC_ROOT}/logs/vps-status.txt}"
LOCAL_JOURNAL_PATH="${MODEL050426_LOCAL_JOURNAL_PATH:-${LOCAL_SYNC_ROOT}/logs/journal-tail.log}"
LOCAL_PYTHON="${MODEL050426_LOCAL_PYTHON:-}"
SSH_OPTIONS="${MODEL050426_SSH_OPTIONS:--o BatchMode=yes -o ConnectTimeout=10}"
JOURNAL_LINES="${MODEL050426_JOURNAL_LINES:-500}"
KEEP_TIMESTAMPED="${MODEL050426_KEEP_TIMESTAMPED:-0}"

timestamp_utc="$(date -u +"%Y%m%dT%H%M%SZ")"
remote_tmp_db="${REMOTE_TMP_DIR}/model050426-signals-${timestamp_utc}.sqlite3"

cleanup_remote() {
    ssh ${SSH_OPTIONS} "${VPS_HOST}" "rm -f '${remote_tmp_db}'" >/dev/null 2>&1 || true
}

trap cleanup_remote EXIT

if [[ -z "${LOCAL_PYTHON}" ]]; then
    if [[ -x "${REPO_ROOT}/.venv/bin/python" ]]; then
        LOCAL_PYTHON="${REPO_ROOT}/.venv/bin/python"
    else
        LOCAL_PYTHON="python3"
    fi
fi

mkdir -p \
    "$(dirname "${LOCAL_DB_PATH}")" \
    "${LOCAL_EXPORT_DIR}" \
    "$(dirname "${LOCAL_REPORT_PATH}")" \
    "$(dirname "${LOCAL_STATUS_PATH}")" \
    "$(dirname "${LOCAL_JOURNAL_PATH}")"

if [[ "${KEEP_TIMESTAMPED}" == "1" ]]; then
    mkdir -p "${LOCAL_SYNC_ROOT}/db/archive" "${LOCAL_SYNC_ROOT}/exports/archive"
fi

echo "[sync] creating SQLite backup on ${VPS_HOST}:${REMOTE_DB_PATH}"
ssh ${SSH_OPTIONS} "${VPS_HOST}" python3 - "${REMOTE_DB_PATH}" "${remote_tmp_db}" <<'PY'
import sqlite3
import sys
from pathlib import Path

source_path = Path(sys.argv[1])
target_path = Path(sys.argv[2])
target_path.parent.mkdir(parents=True, exist_ok=True)
if target_path.exists():
    target_path.unlink()

source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
target = sqlite3.connect(str(target_path))
try:
    source.backup(target)
finally:
    target.close()
    source.close()
PY

echo "[sync] pulling database backup"
rsync -az --partial "${VPS_HOST}:${remote_tmp_db}" "${LOCAL_DB_PATH}"

if [[ "${KEEP_TIMESTAMPED}" == "1" ]]; then
    cp "${LOCAL_DB_PATH}" "${LOCAL_SYNC_ROOT}/db/archive/signals-${timestamp_utc}.sqlite3"
fi

echo "[sync] capturing service status"
ssh ${SSH_OPTIONS} "${VPS_HOST}" \
    "systemctl status ${REMOTE_SERVICE_NAME} --no-pager || true" \
    > "${LOCAL_STATUS_PATH}"

echo "[sync] capturing journal tail"
ssh ${SSH_OPTIONS} "${VPS_HOST}" \
    "journalctl -u ${REMOTE_SERVICE_NAME} -n ${JOURNAL_LINES} --no-pager || true" \
    > "${LOCAL_JOURNAL_PATH}"

if [[ -d "${LOCAL_EXPORT_DIR}" ]]; then
    find "${LOCAL_EXPORT_DIR}" -mindepth 1 -maxdepth 1 -exec rm -rf {} +
fi

echo "[sync] generating local analytics exports"
"${LOCAL_PYTHON}" "${REPORT_SCRIPT}" \
    --db "${LOCAL_DB_PATH}" \
    --export-dir "${LOCAL_EXPORT_DIR}" \
    > "${LOCAL_REPORT_PATH}"

if [[ "${KEEP_TIMESTAMPED}" == "1" ]]; then
    archive_export_dir="${LOCAL_SYNC_ROOT}/exports/archive/${timestamp_utc}"
    mkdir -p "${archive_export_dir}"
    rsync -a "${LOCAL_EXPORT_DIR}/" "${archive_export_dir}/"
    cp "${LOCAL_REPORT_PATH}" "${LOCAL_SYNC_ROOT}/reports/report-${timestamp_utc}.txt"
fi

echo "${timestamp_utc}" > "${LOCAL_SYNC_ROOT}/last_sync_utc.txt"
printf '%s\n' \
    "db=${LOCAL_DB_PATH}" \
    "exports=${LOCAL_EXPORT_DIR}" \
    "report=${LOCAL_REPORT_PATH}" \
    "status=${LOCAL_STATUS_PATH}" \
    "journal=${LOCAL_JOURNAL_PATH}" \
    "timestamp=${timestamp_utc}"
