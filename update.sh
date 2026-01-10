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

if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Bitte als root ausführen: sudo $0"
  exit 1
fi

echo "[1/4] Git Update..."
if [[ -d "$SCRIPT_DIR/.git" ]]; then
  cd "$SCRIPT_DIR"
  git fetch --all --prune
  git pull --ff-only || true
else
  echo "WARN: Kein Git Repo erkannt (.git fehlt). Überspringe git pull."
fi

echo "[2/4] Dependencies Update..."
if [[ ! -d "$SCRIPT_DIR/.venv" ]]; then
  python3 -m venv "$SCRIPT_DIR/.venv"
fi
"$SCRIPT_DIR/.venv/bin/python" -m pip install --upgrade pip
"$SCRIPT_DIR/.venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

echo "[3/4] Restart..."
if command -v systemctl >/dev/null 2>&1; then
  if systemctl list-units --full -all | grep -q "${SERVICE_NAME}.service"; then
    systemctl restart "${SERVICE_NAME}.service"
    echo "  -> systemd restarted"
  else
    echo "WARN: ${SERVICE_NAME}.service nicht gefunden. (Vielleicht nicht installiert?)"
  fi
else
  # rc.local method
  PID_FILE="/data/${SERVICE_NAME}.pid"
  if [[ -f "$PID_FILE" ]]; then
    PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${PID:-}" ]] && kill -0 "$PID" 2>/dev/null; then
      kill "$PID" || true
      sleep 1
    fi
  fi
  echo "  -> starte neu (rc.local mode)..."
  ENV_FILE="$ENV_FILE" "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/ess_accu_off.py" >>"/data/${SERVICE_NAME}.log" 2>&1 &
  echo $! > "$PID_FILE"
fi

echo "[4/4] Done."
echo "Logs (systemd): journalctl -u ${SERVICE_NAME} -f"
echo "Logs (rc.local): tail -f /data/${SERVICE_NAME}.log"
