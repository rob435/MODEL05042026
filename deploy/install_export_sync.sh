#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
APP_ROOT="${MODEL050426_INSTALL_ROOT:-${HOME}/Library/Application Support/MODEL050426-sync}"
BIN_DIR="${APP_ROOT}/bin"
PLIST_PATH="${HOME}/Library/LaunchAgents/com.model050426.export-sync.plist"
LOG_OUT="${HOME}/Library/Logs/model050426-export-sync.log"
LOG_ERR="${HOME}/Library/Logs/model050426-export-sync.err"
SYNC_SCRIPT_PATH="${BIN_DIR}/sync_exports.sh"
REPORT_SCRIPT_PATH="${APP_ROOT}/report.py"
LOCAL_PYTHON_PATH="${MODEL050426_INSTALL_PYTHON:-$(command -v python3)}"
SYNC_HOUR="${MODEL050426_SYNC_HOUR:-9}"
SYNC_MINUTE="${MODEL050426_SYNC_MINUTE:-0}"

mkdir -p "${BIN_DIR}" "${HOME}/Library/LaunchAgents" "${HOME}/Library/Logs"
: > "${LOG_OUT}"
: > "${LOG_ERR}"

cp "${REPO_ROOT}/deploy/sync_exports.sh" "${SYNC_SCRIPT_PATH}"
cp "${REPO_ROOT}/report.py" "${REPORT_SCRIPT_PATH}"
chmod +x "${SYNC_SCRIPT_PATH}"

cat > "${PLIST_PATH}" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>com.model050426.export-sync</string>
  <key>ProgramArguments</key>
  <array>
    <string>${SYNC_SCRIPT_PATH}</string>
  </array>
  <key>RunAtLoad</key>
  <false/>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key>
    <integer>${SYNC_HOUR}</integer>
    <key>Minute</key>
    <integer>${SYNC_MINUTE}</integer>
  </dict>
  <key>EnvironmentVariables</key>
  <dict>
    <key>MODEL050426_REPORT_SCRIPT</key>
    <string>${REPORT_SCRIPT_PATH}</string>
    <key>MODEL050426_LOCAL_PYTHON</key>
    <string>${LOCAL_PYTHON_PATH}</string>
  </dict>
  <key>StandardOutPath</key>
  <string>${LOG_OUT}</string>
  <key>StandardErrorPath</key>
  <string>${LOG_ERR}</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$(id -u)" "${PLIST_PATH}" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "${PLIST_PATH}"
launchctl kickstart -k "gui/$(id -u)/com.model050426.export-sync"

printf '%s\n' \
    "installed_root=${APP_ROOT}" \
    "sync_script=${SYNC_SCRIPT_PATH}" \
    "report_script=${REPORT_SCRIPT_PATH}" \
    "python=${LOCAL_PYTHON_PATH}" \
    "schedule=${SYNC_HOUR}:$(printf '%02d' "${SYNC_MINUTE}")" \
    "plist=${PLIST_PATH}" \
    "stdout_log=${LOG_OUT}" \
    "stderr_log=${LOG_ERR}"
