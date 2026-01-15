#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="victron-ess"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Optional: anderes Python wählen (z.B. PY_BIN=python3.13)
PY_BIN="${PY_BIN:-python3}"

# Default ENV file:
# - Victron Venus OS: /data ist persistent
# - Standard Linux: /etc
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

ensure_python_venv_pkg() {
  # Nur für Debian/Ubuntu Systeme sinnvoll – auf Venus OS i.d.R. nicht verfügbar
  if ! have_cmd apt-get; then
    return 0
  fi

  # Ermittele python version (z.B. 3.13)
  local pyver
  pyver="$($PY_BIN -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)"

  # Versuche erst generisch, dann versionsspezifisch
  echo "-> Versuche venv/ensurepip Abhängigkeiten via apt zu installieren (falls nötig)..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -y >/dev/null 2>&1 || true

  apt-get install -y python3-venv >/dev/null 2>&1 && return 0

  if [[ -n "${pyver:-}" ]]; then
    apt-get install -y "python${pyver}-venv" >/dev/null 2>&1 && return 0
  fi

  return 0
}

venv_is_healthy() {
  # .venv vorhanden + python ausführbar + pip importierbar
  [[ -x "$SCRIPT_DIR/.venv/bin/python" ]] || return 1
  "$SCRIPT_DIR/.venv/bin/python" -c "import pip" >/dev/null 2>&1 || return 1
  return 0
}

create_or_repair_venv() {
  # Falls venv existiert aber kaputt -> löschen
  if [[ -d "$SCRIPT_DIR/.venv" ]] && ! venv_is_healthy; then
    echo "-> Bestehende .venv ist kaputt (pip fehlt o. python nicht ausführbar). Lösche .venv..."
    rm -rf "$SCRIPT_DIR/.venv"
  fi

  # Neu erstellen wenn nicht vorhanden
  if [[ ! -d "$SCRIPT_DIR/.venv" ]]; then
    echo "-> Erstelle venv mit $PY_BIN ..."
    if ! "$PY_BIN" -m venv "$SCRIPT_DIR/.venv" 2>/dev/null; then
      echo "-> python venv fehlgeschlagen. Installiere venv Pakete (Debian/Ubuntu) und versuche erneut..."
      ensure_python_venv_pkg
      "$PY_BIN" -m venv "$SCRIPT_DIR/.venv"
    fi
  fi

  # pip sicherstellen
  if ! "$SCRIPT_DIR/.venv/bin/python" -c "import pip" >/dev/null 2>&1; then
    echo "-> pip fehlt im venv. Führe ensurepip aus..."
    "$SCRIPT_DIR/.venv/bin/python" -m ensurepip --upgrade
  fi
}

echo "[1/5] Python venv & Dependencies..."
create_or_repair_venv
"$SCRIPT_DIR/.venv/bin/python" -m pip install --upgrade pip
"$SCRIPT_DIR/.venv/bin/pip" install -r "$SCRIPT_DIR/requirements.txt"

echo "[2/5] ENV Datei anlegen (falls nicht vorhanden)..."
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ -f "$SCRIPT_DIR/.env.example" ]]; then
    cp "$SCRIPT_DIR/.env.example" "$ENV_FILE"
    chmod 600 "$ENV_FILE" || true
    echo "  -> erstellt: $ENV_FILE (bitte anpassen!)"
  else
    echo "WARN: .env.example fehlt, überspringe ENV Erstellung."
  fi
else
  echo "  -> $ENV_FILE existiert bereits (OK)."
fi

# Service-Installation:
# - Wenn systemd vorhanden: systemd service (Standard Linux)
# - Sonst: /data/rc.local Eintrag (typisch Venus OS)
if have_cmd systemctl; then
  echo "[3/5] systemd Service installieren..."
  cat > "/etc/systemd/system/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=${SERVICE_NAME} (Victron ESS controller)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=${SCRIPT_DIR}
EnvironmentFile=${ENV_FILE}
ExecStart=${SCRIPT_DIR}/.venv/bin/python ${SCRIPT_DIR}/ess_accu_off.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
EOF

  echo "[4/5] systemd reload/enable/start..."
  systemctl daemon-reload
  systemctl enable --now "${SERVICE_NAME}.service"

else
  echo "[3/5] Kein systemd gefunden -> rc.local Installation (Venus OS / embedded)..."

  if [[ ! -d "/data" ]]; then
    echo "ERROR: /data existiert nicht und systemd ist nicht verfügbar. Abbruch."
    exit 1
  fi

  RC_LOCAL="/data/rc.local"
  LOG_FILE="/data/${SERVICE_NAME}.log"
  PID_FILE="/data/${SERVICE_NAME}.pid"

  # WICHTIG: Script lädt env Datei über ESS_ACCU_OFF_ENV_FILE
  START_CMD="ESS_ACCU_OFF_ENV_FILE=\"$ENV_FILE\" \"$SCRIPT_DIR/.venv/bin/python\" \"$SCRIPT_DIR/ess_accu_off.py\" >>\"$LOG_FILE\" 2>&1 & echo \$! > \"$PID_FILE\""

  MARK_START="# >>> ${SERVICE_NAME} >>>"
  MARK_END="# <<< ${SERVICE_NAME} <<<"

  # rc.local anlegen (falls nicht vorhanden)
  if [[ ! -f "$RC_LOCAL" ]]; then
    cat > "$RC_LOCAL" <<'EOF'
#!/bin/bash
# custom startup commands
EOF
    chmod +x "$RC_LOCAL" || true
  fi

  # alten Block entfernen
  if grep -qF "$MARK_START" "$RC_LOCAL"; then
    awk -v s="$MARK_START" -v e="$MARK_END" '
      $0==s {inblock=1; next}
      $0==e {inblock=0; next}
      !inblock {print}
    ' "$RC_LOCAL" > "${RC_LOCAL}.tmp"
    mv "${RC_LOCAL}.tmp" "$RC_LOCAL"
  fi

  # neuen Block anhängen
  {
    echo "$MARK_START"
    echo "# Start ${SERVICE_NAME} on boot"
    echo "$START_CMD"
    echo "$MARK_END"
  } >> "$RC_LOCAL"

  echo "[4/5] Prozess jetzt starten..."
  # Stop falls schon läuft
  if [[ -f "$PID_FILE" ]]; then
    OLD_PID="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [[ -n "${OLD_PID:-}" ]] && kill -0 "$OLD_PID" 2>/dev/null; then
      kill "$OLD_PID" || true
      sleep 1
    fi
  fi
  bash -c "$START_CMD"
fi

echo "[5/5] Fertig."
echo "Config: $ENV_FILE"
echo "Logs (systemd): journalctl -u ${SERVICE_NAME} -f"
echo "Logs (rc.local): tail -f /data/${SERVICE_NAME}.log"
