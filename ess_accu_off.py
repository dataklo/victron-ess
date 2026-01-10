#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
ess-accu-off

Steuert Victron MultiPlus Mode + optional ESS Hub4Mode (ESS1/ESS2) über Modbus TCP.
Komplette Konfiguration erfolgt über Environment-Variablen (optional per .env Datei).

Hinweis:
- Register/Unit IDs können je nach Setup abweichen.
- Schreibzugriffe beeinflussen das Verhalten deines Systems – vorher testen (DRY_RUN=1).
"""

from __future__ import annotations

import os
import time
import logging
import inspect
from dataclasses import dataclass
from typing import Optional, Any, Tuple

from pymodbus.client import ModbusTcpClient


# =========================
# Helpers: .env loader + env parsing
# =========================
def load_env_file(path: str) -> None:
    """
    Minimaler .env Loader (kein extra Dependency).
    - ignoriert leere Zeilen & Kommentare (# ...)
    - akzeptiert 'export KEY=VALUE'
    - überschreibt bestehende ENV nicht (setdefault)
    """
    if not path:
        return
    try:
        with open(path, "r", encoding="utf-8") as f:
            for raw in f:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if line.lower().startswith("export "):
                    line = line[7:].strip()
                if "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k:
                    os.environ.setdefault(k, v)
    except FileNotFoundError:
        return


def env_str(name: str, default: str) -> str:
    return os.getenv(name, default)


def env_int(name: str, default: int) -> int:
    v = os.getenv(name)
    if v is None or v == "":
        return int(default)
    return int(v, 10)


def env_float(name: str, default: float) -> float:
    v = os.getenv(name)
    if v is None or v == "":
        return float(default)
    return float(v)


def env_bool(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None or v == "":
        return bool(default)
    v = v.strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def env_int_tuple(name: str, default: Tuple[int, ...]) -> Tuple[int, ...]:
    v = os.getenv(name)
    if v is None or v.strip() == "":
        return tuple(default)
    parts = [p.strip() for p in v.replace(";", ",").split(",") if p.strip() != ""]
    return tuple(int(p, 10) for p in parts)


# =========================
# Config
# =========================
@dataclass(frozen=True)
class Config:
    # Modbus TCP
    venus_ip: str
    venus_port: int
    modbus_timeout_s: int

    # Enable-Switch
    read_unit_id: int
    reg_enable: int  # uint16

    # Messwerte
    reg_soc: int
    reg_load_base: int            # 3 Register: base, base+1, base+2
    pv_regs: Tuple[int, ...]      # z.B. "811,812,813"

    # MultiPlus Mode
    mode_unit_id: int
    reg_mode: int                 # uint16

    # ESS Mode (Hub4Mode)
    ess_unit_id: int
    reg_ess_mode: int             # uint16
    ess_day_value: int            # ESS1 (mit Phase compensation)
    ess_night_value: int          # ESS2 (ohne Phase compensation)
    ess_allow_night_switch: bool  # NUR für 3-phasige Systeme sinnvoll

    # SOC-Logik
    soc_min: float
    soc_charge_min: float

    # PV/Überschuss-Erkennung
    pv_surplus_w: int
    pv_surplus_confirm_s: int

    # Nachterkennung (Integrator)
    pv_night_w: int
    night_confirm_s: int
    night_decay_factor: float

    # Nachtregel
    turn_off_at_night_when_below_charge_min: bool

    # Abschalt-Sequenz
    off_delay_seconds: int

    # Loop / Schutz
    poll_interval_s: float
    min_write_gap_s: float
    min_ess_write_gap_s: float

    # Skalierung/Offsets
    soc_divisor: float
    addr_offset: int
    dry_run: bool


def load_config() -> Config:
    # optional .env
    # Default: ".env" im gleichen Ordner wie das Script
    default_env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    load_env_file(env_str("ESS_ACCU_OFF_ENV_FILE", default_env_path))

    return Config(
        venus_ip=env_str("VENUS_IP", "192.168.41.101"),
        venus_port=env_int("VENUS_PORT", 502),
        modbus_timeout_s=env_int("MODBUS_TIMEOUT_S", 2),

        read_unit_id=env_int("READ_UNIT_ID", 100),
        reg_enable=env_int("REG_ENABLE", 806),

        reg_soc=env_int("REG_SOC", 843),
        reg_load_base=env_int("REG_LOAD_BASE", 817),
        pv_regs=env_int_tuple("PV_REGS", (811, 812, 813)),

        mode_unit_id=env_int("MODE_UNIT_ID", 227),
        reg_mode=env_int("REG_MODE", 33),

        ess_unit_id=env_int("ESS_UNIT_ID", 100),
        reg_ess_mode=env_int("REG_ESS_MODE", 2902),
        ess_day_value=env_int("ESS_DAY_VALUE", 1),
        ess_night_value=env_int("ESS_NIGHT_VALUE", 2),
        ess_allow_night_switch=env_bool("ESS_ALLOW_NIGHT_SWITCH", False),

        soc_min=env_float("SOC_MIN", 39),
        soc_charge_min=env_float("SOC_CHARGE_MIN", 50),

        pv_surplus_w=env_int("PV_SURPLUS_W", 100),
        pv_surplus_confirm_s=env_int("PV_SURPLUS_CONFIRM_S", 60),

        pv_night_w=env_int("PV_NIGHT_W", 200),
        night_confirm_s=env_int("NIGHT_CONFIRM_S", 1800),
        night_decay_factor=env_float("NIGHT_DECAY_FACTOR", 0.25),

        turn_off_at_night_when_below_charge_min=env_bool("TURN_OFF_AT_NIGHT_WHEN_BELOW_CHARGE_MIN", True),

        off_delay_seconds=env_int("OFF_DELAY_SECONDS", 30),

        poll_interval_s=env_float("POLL_INTERVAL_S", 5.0),
        min_write_gap_s=env_float("MIN_WRITE_GAP_S", 5.0),
        min_ess_write_gap_s=env_float("MIN_ESS_WRITE_GAP_S", 30.0),

        soc_divisor=env_float("SOC_DIVISOR", 1),
        addr_offset=env_int("ADDR_OFFSET", 0),
        dry_run=env_bool("DRY_RUN", False),
    )


# =========================
# Logging
# =========================
def setup_logging() -> None:
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    h = logging.StreamHandler()
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s: %(message)s"))
    logger.handlers = [h]
    logger.propagate = False


# Modi
MODE_CHARGER_ONLY = 1
MODE_ON = 3
MODE_OFF = 4


def mode_name(v: Optional[int]) -> str:
    if v is None:
        return "Unknown"
    return {
        MODE_CHARGER_ONLY: "ChargerOnly",
        MODE_ON: "On",
        MODE_OFF: "Off",
    }.get(v, f"Unknown({v})")


@dataclass
class Measurements:
    soc_percent: float
    load_w: int
    pv_w: int


# =========================
# Modbus-Kompatibilität
# =========================
class ModbusAdapter:
    """
    Passend zu unterschiedlichen pymodbus Versionen:
    - read_holding_registers: count keyword-only
    - unit param kann heißen: device_id / slave / unit / unit_id / dev_id
    """
    def __init__(self, host: str, port: int, timeout_s: int, addr_offset: int, dry_run: bool):
        self.client = ModbusTcpClient(host, port=port, timeout=timeout_s)
        self.addr_offset = int(addr_offset)
        self.dry_run = bool(dry_run)

        self._rh = self.client.read_holding_registers
        self._wr = self.client.write_register

        rh_params = inspect.signature(self._rh).parameters
        wr_params = inspect.signature(self._wr).parameters

        self.unit_kw_r = next((k for k in ("device_id", "slave", "unit", "unit_id", "dev_id") if k in rh_params), None)
        self.unit_kw_w = next((k for k in ("device_id", "slave", "unit", "unit_id", "dev_id") if k in wr_params), None)
        self.count_kw = next((k for k in ("count", "quantity", "qty", "length", "size") if k in rh_params), None)

        logging.info("ModbusAdapter: read unit_kw=%s count_kw=%s | write unit_kw=%s",
                     self.unit_kw_r, self.count_kw, self.unit_kw_w)

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
        addr = int(reg) + self.addr_offset
        kw = {}
        if self.count_kw:
            kw[self.count_kw] = 1
        if self.unit_kw_r:
            kw[self.unit_kw_r] = unit_id
        rr = self._rh(addr, **kw)
        self._check(rr, f"Read error unit={unit_id} reg={reg}")
        return int(rr.registers[0])

    def read_block(self, unit_id: int, base_reg: int, count: int) -> list[int]:
        if not self.count_kw and count != 1:
            return [self.read_u16(unit_id, base_reg + i) for i in range(count)]

        addr = int(base_reg) + self.addr_offset
        kw = {self.count_kw: count} if self.count_kw else {}
        if self.unit_kw_r:
            kw[self.unit_kw_r] = unit_id

        rr = self._rh(addr, **kw)
        self._check(rr, f"Read error unit={unit_id} reg={base_reg} count={count}")

        regs = [int(x) for x in rr.registers[:count]]
        if len(regs) < count:
            return [self.read_u16(unit_id, base_reg + i) for i in range(count)]
        return regs

    def write_u16(self, unit_id: int, reg: int, value: int) -> None:
        if self.dry_run:
            logging.warning("DRY_RUN: würde schreiben unit=%s reg=%s value=%s", unit_id, reg, value)
            return
        addr = int(reg) + self.addr_offset
        kw = {}
        if self.unit_kw_w:
            kw[self.unit_kw_w] = unit_id
        rr = self._wr(addr, int(value), **kw)
        if hasattr(rr, "isError") and rr.isError():
            raise RuntimeError(f"Write error unit={unit_id} reg={reg} value={value}: {rr}")


# =========================
# Messwerte / Reads
# =========================
def get_measurements(mb: ModbusAdapter, cfg: Config) -> Measurements:
    soc_raw = mb.read_u16(cfg.read_unit_id, cfg.reg_soc)
    soc = float(soc_raw) / float(cfg.soc_divisor)

    l = mb.read_block(cfg.read_unit_id, cfg.reg_load_base, 3)  # base/base+1/base+2
    load_w = int(l[0]) + int(l[1]) + int(l[2])

    pv_w = 0
    for r in cfg.pv_regs:
        pv_w += int(mb.read_u16(cfg.read_unit_id, r))

    return Measurements(soc_percent=soc, load_w=int(load_w), pv_w=int(pv_w))


def read_enabled(mb: ModbusAdapter, cfg: Config) -> int:
    return int(mb.read_u16(cfg.read_unit_id, cfg.reg_enable))


def read_current_mode(mb: ModbusAdapter, cfg: Config) -> Optional[int]:
    try:
        return int(mb.read_u16(cfg.mode_unit_id, cfg.reg_mode))
    except Exception as e:
        logging.warning("Mode lesen fehlgeschlagen (unit=%s reg=%s): %s", cfg.mode_unit_id, cfg.reg_mode, e)
        return None


def read_ess_mode(mb: ModbusAdapter, cfg: Config) -> Optional[int]:
    try:
        return int(mb.read_u16(cfg.ess_unit_id, cfg.reg_ess_mode))
    except Exception as e:
        logging.warning("ESS Mode lesen fehlgeschlagen (unit=%s reg=%s): %s", cfg.ess_unit_id, cfg.reg_ess_mode, e)
        return None


def set_mode(mb: ModbusAdapter, cfg: Config, mode: int) -> None:
    mb.write_u16(cfg.mode_unit_id, cfg.reg_mode, int(mode))


def set_ess_mode(mb: ModbusAdapter, cfg: Config, value: int) -> None:
    mb.write_u16(cfg.ess_unit_id, cfg.reg_ess_mode, int(value))


# =========================
# State Machine
# =========================
STATE_OFF = "OFF"
STATE_CHARGING = "CHARGING"      # ChargerOnly tagsüber, bis SOC_CHARGE_MIN
STATE_ON = "ON"                  # ON latched bis SOC_MIN
STATE_OFF_DELAY = "OFF_DELAY"    # ChargerOnly Xs, dann Off


def main() -> None:
    setup_logging()
    cfg = load_config()

    logging.info(
        "Start. Modbus=%s:%s timeout=%ss | Enable: unit=%s reg=%s muss 1 | "
        "Mode: unit=%s reg=%s | ESS: unit=%s reg=%s day=%s night=%s allowNightSwitch=%s | DRY_RUN=%s",
        cfg.venus_ip, cfg.venus_port, cfg.modbus_timeout_s,
        cfg.read_unit_id, cfg.reg_enable,
        cfg.mode_unit_id, cfg.reg_mode,
        cfg.ess_unit_id, cfg.reg_ess_mode, cfg.ess_day_value, cfg.ess_night_value, "yes" if cfg.ess_allow_night_switch else "no",
        "yes" if cfg.dry_run else "no",
    )

    mb = ModbusAdapter(cfg.venus_ip, cfg.venus_port, timeout_s=cfg.modbus_timeout_s,
                       addr_offset=cfg.addr_offset, dry_run=cfg.dry_run)

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

    # Start-State
    state = STATE_OFF
    initialized = False

    while True:
        try:
            if not mb.connect():
                raise RuntimeError("Modbus connect() fehlgeschlagen")

            enabled = read_enabled(mb, cfg)
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

                time.sleep(cfg.poll_interval_s)
                continue

            # Messung
            now = time.monotonic()
            m = get_measurements(mb, cfg)
            current_mode = read_current_mode(mb, cfg)
            current_ess = read_ess_mode(mb, cfg)

            # initial state once
            if not initialized:
                if current_mode == MODE_ON:
                    state = STATE_ON
                elif current_mode == MODE_CHARGER_ONLY:
                    state = STATE_CHARGING
                elif current_mode == MODE_OFF:
                    state = STATE_OFF
                else:
                    state = STATE_OFF
                initialized = True
                logging.info("Initial state=%s (ModeIst=%s)", state, mode_name(current_mode))

            # dt für Integrator
            dt = max(0.0, now - last_loop_ts)
            last_loop_ts = now

            # --- tolerante Nachterkennung ---
            if m.pv_w < cfg.pv_night_w:
                night_accum_s = min(float(cfg.night_confirm_s), night_accum_s + dt)
            else:
                night_accum_s = max(0.0, night_accum_s - dt * float(cfg.night_decay_factor))

            night_detected = night_accum_s >= float(cfg.night_confirm_s)

            if await_next_day and night_detected:
                night_seen_since_shutdown = True

            # --- PV surplus detection (stabil) ---
            pv_surplus = m.pv_w >= (m.load_w + cfg.pv_surplus_w)
            if pv_surplus:
                pv_surplus_since = pv_surplus_since or now
            else:
                pv_surplus_since = None

            pv_surplus_confirmed = (pv_surplus_since is not None) and ((now - pv_surplus_since) >= float(cfg.pv_surplus_confirm_s))

            # --- ESS Mode Sync ---
            # Default: immer ESS_DAY_VALUE (ESS1)
            # Nacht -> nur auf ESS_NIGHT_VALUE (ESS2) wechseln, wenn ESS_ALLOW_NIGHT_SWITCH=1
            desired_ess = cfg.ess_day_value
            if night_detected and cfg.ess_allow_night_switch:
                desired_ess = cfg.ess_night_value

            if (now - last_ess_write_ts) >= float(cfg.min_ess_write_gap_s):
                if current_ess is not None and current_ess != desired_ess:
                    logging.warning("Setze ESS Mode -> %s (war %s)", desired_ess, current_ess)
                    set_ess_mode(mb, cfg, desired_ess)
                    last_ess_write_ts = now
                    current_ess = desired_ess  # optimistisch

            # Status Log
            logging.info(
                "STATE=%s | SOC=%.1f%% | Load=%sW | PV=%sW | Surplus=%s(conf=%s) | "
                "night=%s(acc=%.0fs/%.0fs) | awaitNextDay=%s nightSeen=%s | "
                "ModeIst=%s | ESS=%s->%s (allowNightSwitch=%s)",
                state, m.soc_percent, m.load_w, m.pv_w,
                "yes" if pv_surplus else "no",
                "yes" if pv_surplus_confirmed else "no",
                "yes" if night_detected else "no",
                night_accum_s, float(cfg.night_confirm_s),
                "yes" if await_next_day else "no",
                "yes" if night_seen_since_shutdown else "no",
                mode_name(current_mode),
                current_ess, desired_ess,
                "yes" if cfg.ess_allow_night_switch else "no",
            )

            def can_write() -> bool:
                return (now - last_write_ts) >= float(cfg.min_write_gap_s)

            def write_mode(target: int):
                nonlocal last_write_ts, current_mode
                if current_mode == target:
                    return
                if not can_write():
                    return
                logging.warning("Setze Mode -> %s", mode_name(target))
                set_mode(mb, cfg, target)
                last_write_ts = now
                current_mode = target  # optimistisch

            # ------------- State Machine -------------

            if state == STATE_OFF:
                # nach Abschaltung erst wieder starten, wenn Nacht einmal gesehen wurde
                if await_next_day and not night_seen_since_shutdown:
                    time.sleep(cfg.poll_interval_s)
                    continue

                if pv_surplus_confirmed:
                    # Sonne/Überschuss: wenn SOC < charge-min => ChargerOnly, sonst ON
                    if m.soc_percent < cfg.soc_charge_min:
                        write_mode(MODE_CHARGER_ONLY)
                        state = STATE_CHARGING
                    else:
                        write_mode(MODE_ON)
                        state = STATE_ON

                    await_next_day = False
                    night_seen_since_shutdown = False

                time.sleep(cfg.poll_interval_s)
                continue

            if state == STATE_CHARGING:
                # nachts und SOC < SOC_CHARGE_MIN => OFF bleiben
                if (
                    cfg.turn_off_at_night_when_below_charge_min
                    and night_detected
                    and (m.soc_percent < cfg.soc_charge_min)
                    and not pv_surplus_confirmed
                ):
                    write_mode(MODE_OFF)
                    state = STATE_OFF
                    await_next_day = True
                    night_seen_since_shutdown = True
                    pv_surplus_since = None
                    time.sleep(cfg.poll_interval_s)
                    continue

                # tagsüber normal: ChargerOnly halten bis SOC_CHARGE_MIN erreicht
                write_mode(MODE_CHARGER_ONLY)

                if m.soc_percent >= cfg.soc_charge_min:
                    write_mode(MODE_ON)
                    state = STATE_ON

                time.sleep(cfg.poll_interval_s)
                continue

            if state == STATE_ON:
                write_mode(MODE_ON)

                if m.soc_percent <= cfg.soc_min:
                    # Sequenz starten: ChargerOnly Xs, dann Off
                    write_mode(MODE_CHARGER_ONLY)
                    state = STATE_OFF_DELAY
                    off_delay_start = now

                time.sleep(cfg.poll_interval_s)
                continue

            if state == STATE_OFF_DELAY:
                write_mode(MODE_CHARGER_ONLY)

                if off_delay_start is None:
                    off_delay_start = now

                if (now - off_delay_start) >= float(cfg.off_delay_seconds):
                    write_mode(MODE_OFF)
                    state = STATE_OFF
                    off_delay_start = None

                    await_next_day = True
                    night_seen_since_shutdown = False
                    pv_surplus_since = None

                time.sleep(cfg.poll_interval_s)
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
