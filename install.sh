#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="victron-ess"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

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

echo "[1/5] Python venv & Dependencies..."
if [[ ! -d "$SCRIPT_DIR/.venv" ]]; then
  python3 -m venv "$SCRIPT_DIR/.venv"
fi
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
if command -v systemctl >/dev/null 2>&1; then
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
  START_CMD="ENV_FILE=\"$ENV_FILE\" \"$SCRIPT_DIR/.venv/bin/python\" \"$SCRIPT_DIR/ess_accu_off.py\" >>/data/${SERVICE_NAME}.log 2>&1 & echo \$! > /data/${SERVICE_NAME}.pid"
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
  if [[ -f "/data/${SERVICE_NAME}.pid" ]]; then
    OLD_PID="$(cat "/data/${SERVICE_NAME}.pid" 2>/dev/null || true)"
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
