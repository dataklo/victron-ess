# victron-ess

Python-Script zur Steuerung von **Victron MultiPlus Mode** (On / ChargerOnly / Off) und optional **ESS Hub4Mode (ESS1/ESS2)** per **Modbus TCP**.

Dieses Projekt ist dafür gedacht, auf einem Linux-System (z. B. Raspberry Pi, VM, NUC, Server) oder auf einem Victron-nahen Host zu laufen und über Modbus TCP auf das Venus/GX zuzugreifen.

> ⚠️ Achtung: Schreibzugriffe verändern das Verhalten deines Systems. Erst mit `DRY_RUN=1` testen.

---

## 1) Voraussetzungen

### Ubuntu/Debian
```bash
sudo apt update
sudo apt install -y git python3 python3-venv
# Falls du Python 3.13 nutzt (wie in deinem Log): 
sudo apt install -y python3.13-venv
```

---

## 2) Installation (systemd / normales Linux)

### Repo klonen
```bash
cd ~
git clone https://github.com/dataklo/victron-ess.git
cd victron-ess
chmod +x install.sh update.sh uninstall.sh
```

### Install starten
```bash
sudo ./install.sh
```

Danach liegt die Konfiguration hier:
- **systemd/Linux:** `/etc/victron-ess.env`

Service/Logs:
```bash
sudo systemctl status victron-ess --no-pager
sudo journalctl -u victron-ess -f
```

---

## 3) Installation (ohne systemd / Venus OS / embedded)

Wenn auf deinem System **kein** `systemctl` existiert, nutzt `install.sh` automatisch `rc.local` (typisch bei Venus OS).

Repo idealerweise nach `/data` klonen (persistenter Speicher):
```bash
cd /data
git clone https://github.com/dataklo/victron-ess.git
cd victron-ess
chmod +x install.sh update.sh uninstall.sh
sudo ./install.sh
```

Dann:
- Config: `/data/victron-ess.env`
- Logs:  
```bash
tail -f /data/victron-ess.log
```

---

## 4) Konfiguration (ENV)

### ENV-Datei bearbeiten
**systemd/Linux:**
```bash
sudo nano /etc/victron-ess.env
```

**rc.local/Venus OS:**
```bash
nano /data/victron-ess.env
```

### Wichtiger Hinweis zu Kommentaren in ENV-Dateien (systemd!)
In systemd `EnvironmentFile=` **keine Inline-Kommentare** verwenden, also NICHT:
```env
REG_LOAD_BASE=817  # Kommentar
```
Sondern Kommentar in eine eigene Zeile:
```env
# 3 Register: 817/818/819
REG_LOAD_BASE=817
```

Wenn du bereits Inline-Kommentare hast, kannst du sie auf einmal entfernen:
```bash
sudo sed -i -E 's/[[:space:]]+[;#].*$//' /etc/victron-ess.env
```

### Service nach Config-Änderung neu starten (systemd)
```bash
sudo systemctl restart victron-ess
sudo journalctl -u victron-ess -f
```

---

## 5) ESS1/ESS2 Umschaltung (deine neue Option)

**Hintergrund:** ESS2 ist typischerweise nur bei **3-phasigen** Systemen sinnvoll.

In der ENV:
```env
# 0 = niemals nachts ESS1->ESS2 umschalten (Default, sinnvoll 1-phasig)
# 1 = nachts darf auf ESS2 geschaltet werden (sinnvoll 3-phasig)
ESS_ALLOW_NIGHT_SWITCH=0
```

- `ESS_ALLOW_NIGHT_SWITCH=0`: Script bleibt immer bei `ESS_DAY_VALUE` (z. B. ESS1)
- `ESS_ALLOW_NIGHT_SWITCH=1`: nachts -> `ESS_NIGHT_VALUE` (z. B. ESS2), tagsüber zurück -> `ESS_DAY_VALUE`

---

## 6) Update

### Normaler Update
```bash
cd ~/victron-ess
sudo ./update.sh
```

### Wenn `git pull` wegen lokaler Änderungen abbricht
**Variante A (Änderungen verwerfen):**
```bash
cd ~/victron-ess
git fetch --all --prune
git reset --hard origin/main
sudo ./update.sh
```

**Variante B (Änderungen behalten):**
```bash
cd ~/victron-ess
git stash -u
git pull --ff-only
git stash pop
sudo ./update.sh
```

---

## 7) Uninstall

```bash
cd ~/victron-ess
sudo ./uninstall.sh
```

Optional Cleanup (je nach implementierter uninstall.sh-Option):
```bash
# ENV löschen
sudo UNINSTALL_REMOVE_ENV=1 ./uninstall.sh

# venv löschen
sudo UNINSTALL_REMOVE_VENV=1 ./uninstall.sh
```

---

## 8) Troubleshooting (häufige Fehler)

### A) `No module named pip` / `ensurepip is not available`
Fix:
```bash
sudo apt install -y python3-venv
# ggf. speziell:
sudo apt install -y python3.13-venv

cd ~/victron-ess
sudo rm -rf .venv
sudo ./install.sh
```

### B) Service startet nicht wegen ungültigem int in ENV
Beispiel-Fehler:
```
ValueError: invalid literal for int() with base 10: '817  # ...'
```
Fix: Inline-Kommentare entfernen (siehe oben) und neu starten:
```bash
sudo sed -i -E 's/[[:space:]]+[;#].*$//' /etc/victron-ess.env
sudo systemctl reset-failed victron-ess
sudo systemctl restart victron-ess
sudo journalctl -u victron-ess -f
```

### C) Logs ansehen
**systemd:**
```bash
sudo journalctl -u victron-ess -f
```

**rc.local/Venus OS:**
```bash
tail -f /data/victron-ess.log
```

---

## 9) DRY RUN (erst testen)
In der ENV:
```env
DRY_RUN=1
```

Dann Neustart:
```bash
sudo systemctl restart victron-ess
sudo journalctl -u victron-ess -f
```

---

## Lizenz
MIT
