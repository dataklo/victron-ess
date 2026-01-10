# victron-ess

Python-Script zur Steuerung von **Victron MultiPlus Mode** (On / ChargerOnly / Off) und optional **ESS Hub4Mode (ESS1/ESS2)** per **Modbus TCP**.

Das Script liest SOC, Last und PV über Modbus und schaltet:
- bei PV-Überschuss morgens von **OFF → ChargerOnly/On**
- hält **ChargerOnly**, bis `SOC_CHARGE_MIN` erreicht ist
- hält **On** (latched), bis `SOC_MIN` unterschritten wird
- fährt dann eine Abschalt-Sequenz: **ChargerOnly → warten → Off**
- erkennt Nacht tolerant über einen Integrator
- optional: **ESS1/ESS2 Umschaltung** abhängig von Tag/Nacht

> ⚠️ Achtung: Schreibzugriffe verändern das Verhalten deines Systems. Erst mit `DRY_RUN=1` testen.

---

## Installation (systemd)

1. Repo klonen
2. Konfiguration anlegen
3. Install-Script ausführen

```bash
git clone <DEIN_GITHUB_REPO_URL>
cd victron-ess
sudo ./install.sh
```

Danach:
- Config: `/etc/victron-ess.env`
- Logs: `journalctl -u victron-ess -f`

---

## Update

```bash
cd victron-ess
sudo ./update.sh
```

---

## Uninstall

```bash
cd victron-ess
sudo ./uninstall.sh
```

Optional Cleanup:
- `UNINSTALL_REMOVE_ENV=1 sudo ./uninstall.sh` (löscht `/etc/victron-ess.env`)
- `UNINSTALL_REMOVE_VENV=1 sudo ./uninstall.sh` (löscht `.venv`)

---

## Konfiguration (ENV)

Alle Parameter sind per **Environment Variables** konfigurierbar.  
Du kannst entweder:

- eine `.env` Datei im Repo-Ordner nutzen (wird automatisch geladen), **oder**
- eine Env-Datei via systemd `EnvironmentFile=/etc/victron-ess.env` (Standard im `install.sh`)

Beispiel: siehe [`.env.example`](./.env.example)

### Wichtig: ESS1/ESS2 Umschaltung (3-phasig vs. 1-phasig)

Das Script kann nachts von **ESS1 → ESS2** wechseln (und tagsüber zurück).  
Da **ESS2 typischerweise nur bei 3-phasigen Systemen Sinn macht**, ist das Umschalten standardmäßig **deaktiviert**:

- **1-phasig:** `ESS_ALLOW_NIGHT_SWITCH=0`
- **3-phasig:** `ESS_ALLOW_NIGHT_SWITCH=1`

Wenn `ESS_ALLOW_NIGHT_SWITCH=0`, bleibt das Script bei `ESS_DAY_VALUE` (ESS1) – auch nachts.

---

## ENV Variablen (Default-Werte)

| Variable | Default | Beschreibung |
|---|---:|---|
| `VENUS_IP` | `192.168.41.101` | IP des Venus / GX |
| `VENUS_PORT` | `502` | Modbus TCP Port |
| `MODBUS_TIMEOUT_S` | `2` | Modbus Timeout |
| `READ_UNIT_ID` | `100` | Unit-ID für Messwerte/Enable |
| `REG_ENABLE` | `806` | Enable Register (Script nur aktiv wenn `==1`) |
| `REG_SOC` | `843` | SOC Register |
| `REG_LOAD_BASE` | `817` | Last Register Base (3 Register: base..base+2) |
| `PV_REGS` | `811,812,813` | PV Register (Summe) |
| `MODE_UNIT_ID` | `227` | Unit-ID für MultiPlus Mode |
| `REG_MODE` | `33` | MultiPlus Mode Register |
| `ESS_UNIT_ID` | `100` | Unit-ID für ESS Mode |
| `REG_ESS_MODE` | `2902` | ESS Mode Register (Hub4Mode) |
| `ESS_DAY_VALUE` | `1` | ESS Mode für Tag (ESS1) |
| `ESS_NIGHT_VALUE` | `2` | ESS Mode für Nacht (ESS2) |
| `ESS_ALLOW_NIGHT_SWITCH` | `0` | nachts auf ESS2 wechseln dürfen |
| `SOC_MIN` | `39` | Abschalten wenn SOC <= |
| `SOC_CHARGE_MIN` | `50` | bis dahin ChargerOnly, danach ON |
| `PV_SURPLUS_W` | `100` | PV muss `Load + PV_SURPLUS_W` überschreiten |
| `PV_SURPLUS_CONFIRM_S` | `60` | Überschuss muss so lange stabil sein |
| `PV_NIGHT_W` | `200` | "PV niedrig" Schwelle |
| `NIGHT_CONFIRM_S` | `1800` | Nacht wenn PV überwiegend niedrig (Integrator) |
| `NIGHT_DECAY_FACTOR` | `0.25` | Abbau-Faktor für Integrator bei PV>Schwelle |
| `TURN_OFF_AT_NIGHT_WHEN_BELOW_CHARGE_MIN` | `1` | nachts OFF bleiben wenn SOC < `SOC_CHARGE_MIN` |
| `OFF_DELAY_SECONDS` | `30` | Delay zwischen ChargerOnly und Off |
| `POLL_INTERVAL_S` | `5.0` | Loop Intervall |
| `MIN_WRITE_GAP_S` | `5.0` | min Abstand zwischen Mode Writes |
| `MIN_ESS_WRITE_GAP_S` | `30.0` | min Abstand zwischen ESS Writes |
| `SOC_DIVISOR` | `1` | SOC Skalierung (z.B. 10 bei 650==65.0%) |
| `ADDR_OFFSET` | `0` | Register Offset (bei off-by-one z.B. `-1`) |
| `DRY_RUN` | `0` | wenn `1`: keine Writes, nur Logging |

**Bool Werte:** `1/0`, `true/false`, `yes/no`, `on/off`

---

## Lizenz

Empfehlung: MIT (kannst du nach Bedarf ergänzen).

## Installation

### Auf Victron Venus OS (GX Device)

- Repo nach **/data** klonen (persistenter Speicher)
- `sudo ./install.sh` legt standardmäßig die ENV unter `/data/victron-ess.env` an und trägt einen Start in `/data/rc.local` ein.

### Auf normalem Linux (systemd)

- `sudo ./install.sh` nutzt automatisch systemd, wenn `systemctl` verfügbar ist.
- ENV liegt standardmäßig unter `/etc/victron-ess.env`.
