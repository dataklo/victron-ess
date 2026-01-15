#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import time
import logging
import inspect
from dataclasses import dataclass
from typing import Optional, Any, List, Dict

from pymodbus.client import ModbusTcpClient


# =========================
# ENV / .env Loader
# =========================

# Entfernt Inline-Kommentare am Ende:
# "817   # comment"  -> "817"
# Wichtig: systemd EnvironmentFile übernimmt sonst alles nach '=' als Wert.
_INLINE_COMMENT_RE = re.compile(r"\s+[;#].*$")


def _clean_env_value(v: str) -> str:
    v = v.strip()
    # Optional quotes entfernen
    if len(v) >= 2 and v[0] == v[-1] and v[0] in ("'", '"'):
        v = v[1:-1]
    # Inline-Kommentare abschneiden
    v = _INLINE_COMMENT_RE.sub("", v).strip()
    return v


def load_env_file(path: str, *, override_existing: bool = False) -> None:
    """Lädt KEY=VALUE aus einer Datei ins os.environ.

    - Unterstützt Kommentare (# oder ;)
    - Unterstützt Inline-Kommentare nach Whitespace (z.B. 'A=1  # comment')
    - Überschreibt bestehende Variablen nur wenn override_existing=True
    """
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#") or line.startswith(";"):
                    continue
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = _clean_env_value(v)
                if not k:
                    continue
                if (not override_existing) and (k in os.environ):
                    continue
                os.environ[k] = v
    except FileNotFoundError:
        return


def env_str(name: str, default: str) -> str:
    v = os.getenv(name)
    if v is None:
        return default
    return _clean_env_value(v)


def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None:
        return default
    v = _clean_env_value(v)
    return int(v, 10)


def env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None:
        return default
    v = _clean_env_value(v)
    return float(v)


def env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    v = _clean_env_value(v).lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return default


def env_csv_ints(name: str, default: List[int]) -> List[int]:
    v = os.getenv(name)
    if v is None:
        return default
    v = _clean_env_value(v)
    parts = [p.strip() for p in v.split(",") if p.strip()]
    if not parts:
        return default
    return [int(p, 10) for p in parts]


def _bootstrap_env() -> None:
    """Optional: lädt ENV aus Datei, wenn angegeben oder .env existiert."""
    # 1) explizit gesetzte Datei (z.B. rc.local Installation)
    env_file = os.getenv("ESS_ACCU_OFF_ENV_FILE")
    override = env_bool("ESS_ACCU_OFF_ENV_OVERRIDE", False)

    if env_file:
        load_env_file(env_file, override_existing=override)
        return

    # 2) .env neben dem Script (praktisch fürs manuelle Starten)
    here = os.path.dirname(os.path.abspath(__file__))
    dotenv = os.path.join(here, ".env")
    load_env_file(dotenv, override_existing=override)


_bootstrap_env()


# =========================
# Konfiguration (aus ENV)
# =========================

# Modbus TCP
VENUS_IP = env_str("VENUS_IP", "192.168.41.101")
VENUS_PORT = env_int("VENUS_PORT", 502)
MODBUS_TIMEOUT_S = env_int("MODBUS_TIMEOUT_S", 2)

# Enable-Switch: Script arbeitet nur wenn == 1
READ_UNIT_ID = env_int("READ_UNIT_ID", 100)
REG_ENABLE = env_int("REG_ENABLE", 806)

# Messwerte
REG_SOC = env_int("REG_SOC", 843)
REG_LOAD_BASE = env_int("REG_LOAD_BASE", 817)  # 3 Register: base..base+2
PV_REGS = env_csv_ints("PV_REGS", [811, 812, 813])

# MultiPlus Mode (VE.Bus)
MODE_UNIT_ID = env_int("MODE_UNIT_ID", 227)
REG_MODE = env_int("REG_MODE", 33)

MODE_CHARGER_ONLY = env_int("MODE_CHARGER_ONLY", 1)
MODE_ON = env_int("MODE_ON", 3)
MODE_OFF = env_int("MODE_OFF", 4)

# ESS Mode (Hub4Mode)
ESS_UNIT_ID = env_int("ESS_UNIT_ID", 100)
REG_ESS_MODE = env_int("REG_ESS_MODE", 2902)
ESS_DAY_VALUE = env_int("ESS_DAY_VALUE", 1)      # ESS1
ESS_NIGHT_VALUE = env_int("ESS_NIGHT_VALUE", 2)  # ESS2
ESS_ALLOW_NIGHT_SWITCH = env_bool("ESS_ALLOW_NIGHT_SWITCH", False)

# SOC / Logik
SOC_MIN = env_float("SOC_MIN", 39)
SOC_CHARGE_MIN = env_float("SOC_CHARGE_MIN", 50)

PV_SURPLUS_W = env_int("PV_SURPLUS_W", 100)
PV_SURPLUS_CONFIRM_S = env_int("PV_SURPLUS_CONFIRM_S", 60)

# Nacht-Erkennung (Integrator)
PV_NIGHT_W = env_int("PV_NIGHT_W", 200)
NIGHT_CONFIRM_S = env_int("NIGHT_CONFIRM_S", 1800)
NIGHT_DECAY_FACTOR = env_float("NIGHT_DECAY_FACTOR", 0.25)

TURN_OFF_AT_NIGHT_WHEN_BELOW_CHARGE_MIN = env_bool(
    "TURN_OFF_AT_NIGHT_WHEN_BELOW_CHARGE_MIN", True
)

OFF_DELAY_SECONDS = env_int("OFF_DELAY_SECONDS", 30)

# Loop / Schutz
POLL_INTERVAL_S = env_float("POLL_INTERVAL_S", 5.0)
MIN_WRITE_GAP_S = env_float("MIN_WRITE_GAP_S", 5.0)
MIN_ESS_WRITE_GAP_S = env_float("MIN_ESS_WRITE_GAP_S", 30.0)

# Skalierung/Offsets
SOC_DIVISOR = env_float("SOC_DIVISOR", 1)
ADDR_OFFSET = env_int("ADDR_OFFSET", 0)
DRY_RUN = env_bool("DRY_RUN", False)

LOG_LEVEL = env_str("LOG_LEVEL", "INFO").upper()


# =========================
# Logging
# =========================
def setup_logging() -> None:
    logger = logging.getLogger()
    logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.handlers = [h]
    logger.propagate = False


def mode_name(v: Optional[int]) -> str:
    if v is None:
        return "Unknown"
    if v == MODE_OFF:
        return "Off"
    if v == MODE_CHARGER_ONLY:
        return "ChargerOnly"
    if v == MODE_ON:
        return "On"
    return str(v)


# =========================
# Modbus-Kompatibilität
# =========================
class ModbusAdapter:
    """Wrapper, der mit verschiedenen pymodbus Signaturen klarkommt."""

    def __init__(self, host: str, port: int, timeout: int = 2):
        self.client = ModbusTcpClient(host, port=port, timeout=timeout)
        self._rh = self.client.read_holding_registers
        self._wr = self.client.write_register

        rh_params = inspect.signature(self._rh).parameters
        wr_params = inspect.signature(self._wr).parameters

        # Je nach pymodbus-Version heißen die args unterschiedlich
        self.unit_kw_r = next(
            (k for k in ("device_id", "slave", "unit", "unit_id", "dev_id") if k in rh_params),
            None,
        )
        self.unit_kw_w = next(
            (k for k in ("device_id", "slave", "unit", "unit_id", "dev_id") if k in wr_params),
            None,
        )
        self.count_kw = next(
            (k for k in ("count", "quantity", "qty", "length", "size") if k in rh_params),
            None,
        )

        logging.info(
            "ModbusAdapter: read unit_kw=%s count_kw=%s | write unit_kw=%s",
            self.unit_kw_r,
            self.count_kw,
            self.unit_kw_w,
        )

    def connect(self) -> bool:
        return self.client.connect()

    def close(self) -> None:
        try:
            self.client.close()
        except Exception:
            pass

    @staticmethod
    def _check(rr: Any, what: str) -> None:
        if hasattr(rr, "isError") and rr.isError():
            raise RuntimeError(f"{what}: {rr}")

    def read_u16(self, unit_id: int, reg: int) -> int:
        addr = int(reg) + int(ADDR_OFFSET)
        kw: Dict[str, Any] = {}
        if self.count_kw:
            kw[self.count_kw] = 1
        if self.unit_kw_r:
            kw[self.unit_kw_r] = int(unit_id)
        rr = self._rh(addr, **kw)
        self._check(rr, f"Read error unit={unit_id} reg={reg}")
        return int(rr.registers[0])

    def read_block(self, unit_id: int, base_reg: int, count: int) -> List[int]:
        # Fallback: wenn read_holding_registers keine count kw hat
        if not self.count_kw and count != 1:
            return [self.read_u16(unit_id, base_reg + i) for i in range(count)]

        addr = int(base_reg) + int(ADDR_OFFSET)
        kw: Dict[str, Any] = {self.count_kw: int(count)} if self.count_kw else {}
        if self.unit_kw_r:
            kw[self.unit_kw_r] = int(unit_id)

        rr = self._rh(addr, **kw)
        self._check(rr, f"Read error unit={unit_id} reg={base_reg} count={count}")

        regs = [int(x) for x in rr.registers[:count]]
        if len(regs) < count:
            # Safety fallback
            return [self.read_u16(unit_id, base_reg + i) for i in range(count)]
        return regs

    def write_u16(self, unit_id: int, reg: int, value: int) -> None:
        if DRY_RUN:
            logging.warning("DRY_RUN: würde schreiben unit=%s reg=%s value=%s", unit_id, reg, value)
            return
        addr = int(reg) + int(ADDR_OFFSET)
        kw: Dict[str, Any] = {}
        if self.unit_kw_w:
            kw[self.unit_kw_w] = int(unit_id)
        rr = self._wr(addr, int(value), **kw)
        if hasattr(rr, "isError") and rr.isError():
            raise RuntimeError(f"Write error unit={unit_id} reg={reg} value={value}: {rr}")


# =========================
# Messwerte / Reads
# =========================
@dataclass
class Measurements:
    soc_percent: float
    load_w: int
    pv_w: int


def get_measurements(mb: ModbusAdapter) -> Measurements:
    soc_raw = mb.read_u16(READ_UNIT_ID, REG_SOC)
    divisor = SOC_DIVISOR if SOC_DIVISOR != 0 else 1.0
    soc = float(soc_raw) / float(divisor)

    l = mb.read_block(READ_UNIT_ID, REG_LOAD_BASE, 3)
    load_w = int(l[0]) + int(l[1]) + int(l[2])

    pv_w = 0
    for r in PV_REGS:
        pv_w += int(mb.read_u16(READ_UNIT_ID, int(r)))

    return Measurements(soc_percent=float(soc), load_w=int(load_w), pv_w=int(pv_w))


def read_enabled(mb: ModbusAdapter) -> int:
    return int(mb.read_u16(READ_UNIT_ID, REG_ENABLE))


def read_current_mode(mb: ModbusAdapter) -> Optional[int]:
    try:
        return int(mb.read_u16(MODE_UNIT_ID, REG_MODE))
    except Exception as e:
        logging.warning("Mode lesen fehlgeschlagen (unit=%s reg=%s): %s", MODE_UNIT_ID, REG_MODE, e)
        return None


def read_ess_mode(mb: ModbusAdapter) -> Optional[int]:
    try:
        return int(mb.read_u16(ESS_UNIT_ID, REG_ESS_MODE))
    except Exception as e:
        logging.warning("ESS Mode lesen fehlgeschlagen (unit=%s reg=%s): %s", ESS_UNIT_ID, REG_ESS_MODE, e)
        return None


def set_mode(mb: ModbusAdapter, mode: int) -> None:
    mb.write_u16(MODE_UNIT_ID, REG_MODE, int(mode))


def set_ess_mode(mb: ModbusAdapter, value: int) -> None:
    mb.write_u16(ESS_UNIT_ID, REG_ESS_MODE, int(value))


# =========================
# State Machine
# =========================
STATE_OFF = "OFF"
STATE_CHARGING = "CHARGING"      # ChargerOnly tagsüber, bis SOC_CHARGE_MIN
STATE_ON = "ON"                  # ON latched bis SOC_MIN
STATE_OFF_DELAY = "OFF_DELAY"    # ChargerOnly OFF_DELAY_SECONDS, dann Off


def main() -> None:
    setup_logging()
    logging.info(
        "Start. Enable: unit=%s reg=%s muss 1 | Mode: unit=%s reg=%s | ESS: unit=%s reg=%s | ESS night switch=%s",
        READ_UNIT_ID,
        REG_ENABLE,
        MODE_UNIT_ID,
        REG_MODE,
        ESS_UNIT_ID,
        REG_ESS_MODE,
        "allowed" if ESS_ALLOW_NIGHT_SWITCH else "disabled",
    )

    mb = ModbusAdapter(VENUS_IP, VENUS_PORT, timeout=int(MODBUS_TIMEOUT_S))

    last_write_ts = 0.0
    last_ess_write_ts = 0.0

    # PV surplus confirm timer
    pv_surplus_since: Optional[float] = None

    # Tolerante Night-Erkennung (Integrator)
    last_loop_ts = time.monotonic()
    night_accum_s = 0.0

    # Nach Abschaltung: erst wieder starten, wenn "Nacht einmal gesehen" wurde
    await_next_day = False
    night_seen_since_shutdown = False

    # OFF delay
    off_delay_start: Optional[float] = None

    # State
    state = STATE_OFF
    initialized = False

    while True:
        try:
            if not mb.connect():
                raise RuntimeError("Modbus connect() fehlgeschlagen")

            enabled = read_enabled(mb)
            if enabled != 1:
                logging.info("Deaktiviert (Enable=%s). Keine Aktionen.", enabled)

                pv_surplus_since = None
                await_next_day = False
                night_seen_since_shutdown = False
                off_delay_start = None
                state = STATE_OFF
                initialized = False

                last_loop_ts = time.monotonic()
                night_accum_s = 0.0

                time.sleep(float(POLL_INTERVAL_S))
                continue

            # Messung
            now = time.monotonic()
            m = get_measurements(mb)
            current_mode = read_current_mode(mb)
            current_ess = read_ess_mode(mb)

            # initial state once
            if not initialized:
                if current_mode == MODE_ON:
                    state = STATE_ON
                elif current_mode == MODE_CHARGER_ONLY:
                    state = STATE_CHARGING
                else:
                    state = STATE_OFF
                initialized = True
                logging.info("Initial state=%s (ModeIst=%s)", state, mode_name(current_mode))

            # dt für Integrator
            dt = max(0.0, now - last_loop_ts)
            last_loop_ts = now

            # --- tolerante Nachterkennung ---
            if m.pv_w < PV_NIGHT_W:
                night_accum_s = min(float(NIGHT_CONFIRM_S), night_accum_s + dt)
            else:
                night_accum_s = max(0.0, night_accum_s - dt * float(NIGHT_DECAY_FACTOR))

            night_detected = night_accum_s >= float(NIGHT_CONFIRM_S)

            if await_next_day and night_detected:
                night_seen_since_shutdown = True

            # --- PV surplus detection (stabil) ---
            pv_surplus = m.pv_w >= (m.load_w + int(PV_SURPLUS_W))
            if pv_surplus:
                pv_surplus_since = pv_surplus_since or now
            else:
                pv_surplus_since = None

            pv_surplus_confirmed = (pv_surplus_since is not None) and (
                (now - pv_surplus_since) >= int(PV_SURPLUS_CONFIRM_S)
            )

            # --- ESS Mode Sync ---
            if ESS_ALLOW_NIGHT_SWITCH:
                desired_ess = ESS_NIGHT_VALUE if night_detected else ESS_DAY_VALUE
            else:
                desired_ess = ESS_DAY_VALUE

            if (now - last_ess_write_ts) >= float(MIN_ESS_WRITE_GAP_S):
                if current_ess is not None and current_ess != desired_ess:
                    logging.warning("Setze ESS Mode -> %s (war %s)", desired_ess, current_ess)
                    set_ess_mode(mb, desired_ess)
                    last_ess_write_ts = now
                    current_ess = desired_ess  # optimistisch

            # Status Log
            logging.info(
                "STATE=%s | SOC=%.1f%% | Load=%sW | PV=%sW | Surplus=%s (%s >=%ss) | Night=%s (%.0f/%.0fs) | awaitNextDay=%s nightSeen=%s | ModeIst=%s | ESS=%s->%s",
                state,
                m.soc_percent,
                m.load_w,
                m.pv_w,
                "yes" if pv_surplus else "no",
                "yes" if pv_surplus_confirmed else "no",
                int(PV_SURPLUS_CONFIRM_S),
                "yes" if night_detected else "no",
                night_accum_s,
                float(NIGHT_CONFIRM_S),
                "yes" if await_next_day else "no",
                "yes" if night_seen_since_shutdown else "no",
                mode_name(current_mode),
                current_ess,
                desired_ess,
            )

            def can_write() -> bool:
                return (now - last_write_ts) >= float(MIN_WRITE_GAP_S)

            def write_mode(target: int) -> None:
                nonlocal last_write_ts, current_mode
                if current_mode == target:
                    return
                if not can_write():
                    return
                logging.warning("Setze Mode -> %s", mode_name(target))
                set_mode(mb, target)
                last_write_ts = now
                current_mode = target  # optimistisch

            # ------------- State Machine -------------

            if state == STATE_OFF:
                # nach Abschaltung erst wieder starten, wenn Nacht einmal gesehen wurde
                if await_next_day and not night_seen_since_shutdown:
                    time.sleep(float(POLL_INTERVAL_S))
                    continue

                if pv_surplus_confirmed:
                    # Sonne/Überschuss: wenn SOC < charge-min => ChargerOnly, sonst ON
                    if m.soc_percent < float(SOC_CHARGE_MIN):
                        write_mode(MODE_CHARGER_ONLY)
                        state = STATE_CHARGING
                    else:
                        write_mode(MODE_ON)
                        state = STATE_ON

                    await_next_day = False
                    night_seen_since_shutdown = False

                time.sleep(float(POLL_INTERVAL_S))
                continue

            if state == STATE_CHARGING:
                # nachts und SOC < SOC_CHARGE_MIN => OFF bleiben (kein Laden nachts)
                if (
                    TURN_OFF_AT_NIGHT_WHEN_BELOW_CHARGE_MIN
                    and night_detected
                    and (m.soc_percent < float(SOC_CHARGE_MIN))
                    and (not pv_surplus_confirmed)
                ):
                    write_mode(MODE_OFF)
                    state = STATE_OFF
                    await_next_day = True
                    night_seen_since_shutdown = True  # Nacht ist ja gerade erkannt
                    pv_surplus_since = None
                    time.sleep(float(POLL_INTERVAL_S))
                    continue

                # tagsüber normal: ChargerOnly halten bis SOC_CHARGE_MIN erreicht
                write_mode(MODE_CHARGER_ONLY)

                if m.soc_percent >= float(SOC_CHARGE_MIN):
                    write_mode(MODE_ON)
                    state = STATE_ON

                time.sleep(float(POLL_INTERVAL_S))
                continue

            if state == STATE_ON:
                write_mode(MODE_ON)

                if m.soc_percent <= float(SOC_MIN):
                    # Sequenz starten: ChargerOnly OFF_DELAY_SECONDS, dann Off
                    write_mode(MODE_CHARGER_ONLY)
                    state = STATE_OFF_DELAY
                    off_delay_start = now

                time.sleep(float(POLL_INTERVAL_S))
                continue

            if state == STATE_OFF_DELAY:
                write_mode(MODE_CHARGER_ONLY)

                if off_delay_start is None:
                    off_delay_start = now

                if (now - off_delay_start) >= int(OFF_DELAY_SECONDS):
                    write_mode(MODE_OFF)
                    state = STATE_OFF
                    off_delay_start = None

                    await_next_day = True
                    night_seen_since_shutdown = False
                    pv_surplus_since = None

                time.sleep(float(POLL_INTERVAL_S))
                continue

        except KeyboardInterrupt:
            logging.info("Beendet (Ctrl+C).")
            break
        except Exception as e:
            logging.error("Fehler: %s", e, exc_info=True)
            mb.close()
            time.sleep(2.0)


if __name__ == "__main__":
    main()
