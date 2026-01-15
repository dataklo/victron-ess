#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="victron-ess"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PY_BIN="${PY_BIN:-python3}"

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

have_cmd() { command -v "$1" >/dev/null 2>&1; }

venv_is_healthy() {
  [[ -x "$SCRIPT_DIR/.venv/bin/python" ]] || return 1
  "$SCRIPT_DIR/.venv/bin/python" -c "import pip" >/dev/null 2>&1 || return 1
  return 0
}

ensure_python_venv_pkg() {
  if ! have_cmd apt-get; then
    return 0
  fi
  local pyver
  pyver="$($PY_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y >/dev/null 2>&1 || true
  apt-get install -y python3-venv >/dev/null 2>&1 && return 0
  if [[ -n "${pyver:-}" ]]; then
    apt-get install -y "python${pyver}-venv" >/dev/null 2>&1 && return 0
  fi
  return 0
}

create_or_repair_venv() {
  if [[ -d "$SCRIPT_DIR/.venv" ]] && ! venv_is_healthy; then
    echo "-> Bestehende .venv ist kaputt (pip fehlt). Lösche .venv..."
    rm -rf "$SCRIPT_DIR/.venv"
  fi

  if [[ ! -d "$SCRIPT_DIR/.venv" ]]; then
    echo "-> Erstelle venv mit $PY_BIN ..."
    if ! "$PY_BIN" -m venv "$SCRIPT_DIR/.venv" 2>/dev/null; then
      echo "-> python venv fehlgeschlagen. Installiere venv Pakete (Debian/Ubuntu) und versuche erneut..."
      ensure_python_venv_pkg
      "$PY_BIN" -m venv "$SCRIPT_DIR/.venv"
    fi
  fi

  if ! "$SCRIPT_DIR/.venv/bin/python" -c "import pip" >/dev/null 2>&1; then
    echo "-> pip fehlt im venv. Führe ensurepip aus..."
    "$SCRIPT_DIR/.venv/bin/python" -m ensurepip --upgrade
  fi
}

echo "[1/4] Git Update..."
if [[ -d "$SCRIPT_DIR/.git" ]]; then
  cd "$SCRIPT_DIR"
  git fetch --all --prune
  git pull --ff-only || true
else
  echo "WARN: Kein Git Repo erkannt (.git fehlt). Überspringe git pull."
fi

echo "[2/4] Dependencies Update..."
create_or_repair_venv
"$SCRIPT_DIR/.venv/bin/python" -m pip install --upgrade pip
"$SCRIPT_DIR/.venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

echo "[3/4] Restart..."
if have_cmd systemctl; then
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
  ESS_ACCU_OFF_ENV_FILE="$ENV_FILE" "$SCRIPT_DIR/.venv/bin/python" "$SCRIPT_DIR/ess_accu_off.py" >>"/data/${SERVICE_NAME}.log" 2>&1 &
  echo $! > "$PID_FILE"
fi

echo "[4/4] Done."
echo "Logs (systemd): journalctl -u ${SERVICE_NAME} -f"
echo "Logs (rc.local): tail -f /data/${SERVICE_NAME}.log"
