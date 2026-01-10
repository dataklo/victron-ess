#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="victron-ess"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -d "/data" ]]; then
  ENV_FILE_DEFAULT="/data/${SERVICE_NAME}.env"
else
  ENV_FILE_DEFAULT="/etc/${SERVICE_NAME}.env"
fi
ENV_FILE="${ENV_FILE:-$ENV_FILE_DEFAULT}"

REMOVE_ENV="${UNINSTALL_REMOVE_ENV:-0}"
REMOVE_VENV="${UNINSTALL_REMOVE_VENV:-0}"

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Bitte als root ausführen: sudo $0"
  exit 1
fi

echo "[1/4] Stop & Disable..."
if command -v systemctl >/dev/null 2>&1 && systemctl list-units --full -all | grep -q "${SERVICE_NAME}.service"; then
  systemctl disable --now "${SERVICE_NAME}.service" || true
  rm -f "/etc/systemd/system/${SERVICE_NAME}.service" || true
  systemctl daemon-reload || true
else
  # rc.local mode: stop pid
  PID_FILE="/data/${SERVICE_NAME}.pid"
  if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
      kill "$PID" || true
      sleep 1
    fi
    rm -f "$PID_FILE" || true
  fi

  # remove rc.local block
  RC_LOCAL="/data/rc.local"
  MARK_START="# >>> ${SERVICE_NAME} >>>"
  MARK_END="# <<< ${SERVICE_NAME} <<<"
  if [[ -f "$RC_LOCAL" ]] && grep -qF "$MARK_START" "$RC_LOCAL"; then
    awk -v s="$MARK_START" -v e="$MARK_END" '
      $0==s {inblock=1; next}
      $0==e {inblock=0; next}
      !inblock {print}
    ' "$RC_LOCAL" > "${RC_LOCAL}.tmp"
    mv "${RC_LOCAL}.tmp" "$RC_LOCAL"
    echo "  -> rc.local Eintrag entfernt"
  fi
fi

echo "[2/4] Optional: ENV entfernen?"
if [[ "$REMOVE_ENV" == "1" ]]; then
  rm -f "$ENV_FILE" || true
  echo "  -> $ENV_FILE entfernt"
else
  echo "  -> $ENV_FILE bleibt erhalten (UNINSTALL_REMOVE_ENV=1 zum Entfernen)"
fi

echo "[3/4] Optional: venv entfernen?"
if [[ "$REMOVE_VENV" == "1" ]]; then
  rm -rf "$SCRIPT_DIR/.venv" || true
  echo "  -> $SCRIPT_DIR/.venv entfernt"
else
  echo "  -> venv bleibt erhalten (UNINSTALL_REMOVE_VENV=1 zum Entfernen)"
fi

echo "[4/4] Fertig."
