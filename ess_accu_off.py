#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import time
import logging
import inspect
from dataclasses import dataclass
from typing import Optional, Any
from pymodbus.client import ModbusTcpClient

# =========================
# KONFIG
# =========================
VENUS_IP = "192.168.41.101"
VENUS_PORT = 502

# Enable-Switch: Script arbeitet nur wenn == 1
READ_UNIT_ID = 100
REG_ENABLE = 806  # uint16

# Messwerte (Unit 100)
REG_SOC = 843
REG_LOAD_BASE = 817             # 817/818/819 Summe
PV_REGS = (811, 812, 813)       # falls wirklich 811,812,812 -> (811, 812, 812)

# MultiPlus Mode (Unit 227)
MODE_UNIT_ID = 227
REG_MODE = 33                   # uint16

# NEU: ESS Mode (Hub4Mode)
ESS_UNIT_ID = 100               # Annahme: liegt auf Unit 100
REG_ESS_MODE = 2902             # uint16
ESS_DAY_VALUE = 1               # ESS with Phase Compensation
ESS_NIGHT_VALUE = 2             # ESS without phase compensation

# SOC-Logik
SOC_MIN = 39            # minsoc (unter/gleich => Abschaltsequenz)
SOC_CHARGE_MIN = 50     # charger-min-soc (ab hier ON-latch)

# PV/Überschuss-Erkennung (morgens Sonne geht auf)
PV_SURPLUS_W = 100              # PV muss >= Load + PV_SURPLUS_W sein
PV_SURPLUS_CONFIRM_S = 60       # Bedingung muss so lange am Stück gelten

# Tolerante Nachterkennung (Integrator)
PV_NIGHT_W = 200                # "PV niedrig" Schwelle
NIGHT_CONFIRM_S = 1800          # Nacht wenn überwiegend 30min PV niedrig
NIGHT_DECAY_FACTOR = 0.25       # wie schnell night_accum bei PV>Schwelle abnimmt

# Nachtregel:
# Wenn SOC < SOC_CHARGE_MIN und Nacht erkannt ist -> OFF bleiben (kein Laden nachts)
TURN_OFF_AT_NIGHT_WHEN_BELOW_CHARGE_MIN = True

# Abschalt-Sequenz
OFF_DELAY_SECONDS = 30          # erst ChargerOnly, dann 30s warten, dann Off

# Loop / Schutz
POLL_INTERVAL_S = 5.0
MIN_WRITE_GAP_S = 5.0           # min. Abstand zwischen Writes (Mode)
MIN_ESS_WRITE_GAP_S = 30.0      # min. Abstand zwischen ESS-Mode Writes

# Skalierung/Offsets
SOC_DIVISOR = 1                 # falls SOC 650 == 65.0% -> 10 setzen
ADDR_OFFSET = 0                 # falls Register off-by-one -> -1 testen
DRY_RUN = False                 # True => keine Writes

# Modi
MODE_CHARGER_ONLY = 1
MODE_ON = 3
MODE_OFF = 4


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
    Passend zu deiner Umgebung:
    - read_holding_registers: count keyword-only
    - unit param bei dir häufig: device_id
    """
    def __init__(self, host: str, port: int, timeout: int = 2):
        self.client = ModbusTcpClient(host, port=port, timeout=timeout)
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
        addr = reg + ADDR_OFFSET
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

        addr = base_reg + ADDR_OFFSET
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
        if DRY_RUN:
            logging.warning("DRY_RUN: würde schreiben unit=%s reg=%s value=%s", unit_id, reg, value)
            return
        addr = reg + ADDR_OFFSET
        kw = {}
        if self.unit_kw_w:
            kw[self.unit_kw_w] = unit_id
        rr = self._wr(addr, int(value), **kw)
        if hasattr(rr, "isError") and rr.isError():
            raise RuntimeError(f"Write error unit={unit_id} reg={reg} value={value}: {rr}")


# =========================
# Messwerte / Reads
# =========================
def get_measurements(mb: ModbusAdapter) -> Measurements:
    soc_raw = mb.read_u16(READ_UNIT_ID, REG_SOC)
    soc = float(soc_raw) / float(SOC_DIVISOR)

    l = mb.read_block(READ_UNIT_ID, REG_LOAD_BASE, 3)  # 817/818/819
    load_w = int(l[0]) + int(l[1]) + int(l[2])

    pv_w = 0
    for r in PV_REGS:
        pv_w += int(mb.read_u16(READ_UNIT_ID, r))

    return Measurements(soc_percent=soc, load_w=int(load_w), pv_w=int(pv_w))


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
STATE_OFF_DELAY = "OFF_DELAY"    # ChargerOnly 30s, dann Off


def main() -> None:
    setup_logging()
    logging.info("Start. Enable: unit=%s reg=%s muss 1. Mode: unit=%s reg=%s | ESS Mode: unit=%s reg=%s",
                 READ_UNIT_ID, REG_ENABLE, MODE_UNIT_ID, REG_MODE, ESS_UNIT_ID, REG_ESS_MODE)

    mb = ModbusAdapter(VENUS_IP, VENUS_PORT, timeout=2)

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

                time.sleep(POLL_INTERVAL_S)
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
            if m.pv_w < PV_NIGHT_W:
                night_accum_s = min(float(NIGHT_CONFIRM_S), night_accum_s + dt)
            else:
                night_accum_s = max(0.0, night_accum_s - dt * NIGHT_DECAY_FACTOR)

            night_detected = night_accum_s >= float(NIGHT_CONFIRM_S)

            if await_next_day and night_detected:
                night_seen_since_shutdown = True

            # --- PV surplus detection (stabil) ---
            pv_surplus = m.pv_w >= (m.load_w + PV_SURPLUS_W)
            if pv_surplus:
                pv_surplus_since = pv_surplus_since or now
            else:
                pv_surplus_since = None

            pv_surplus_confirmed = (pv_surplus_since is not None) and ((now - pv_surplus_since) >= PV_SURPLUS_CONFIRM_S)

            # --- ESS Mode Sync ---
            # Nacht -> 2, Tag -> 1
            desired_ess = ESS_NIGHT_VALUE if night_detected else ESS_DAY_VALUE
            if (now - last_ess_write_ts) >= MIN_ESS_WRITE_GAP_S:
                if current_ess is not None and current_ess != desired_ess:
                    logging.warning("Setze ESS Mode -> %s (war %s)", desired_ess, current_ess)
                    set_ess_mode(mb, desired_ess)
                    last_ess_write_ts = now
                    current_ess = desired_ess  # optimistisch

            # Status Log
            logging.info(
                "STATE=%s | SOC=%.1f%% | Load=%sW | PV=%sW | Surplus=%s(conf=%s) | night=%s(acc=%.0fs/%.0fs) | awaitNextDay=%s nightSeen=%s | ModeIst=%s | ESS=%s->%s",
                state, m.soc_percent, m.load_w, m.pv_w,
                "yes" if pv_surplus else "no",
                "yes" if pv_surplus_confirmed else "no",
                "yes" if night_detected else "no",
                night_accum_s, float(NIGHT_CONFIRM_S),
                "yes" if await_next_day else "no",
                "yes" if night_seen_since_shutdown else "no",
                mode_name(current_mode),
                current_ess, desired_ess
            )

            def can_write() -> bool:
                return (now - last_write_ts) >= MIN_WRITE_GAP_S

            def write_mode(target: int):
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
                    time.sleep(POLL_INTERVAL_S)
                    continue

                if pv_surplus_confirmed:
                    # Sonne/Überschuss: wenn SOC < charge-min => ChargerOnly, sonst ON
                    if m.soc_percent < SOC_CHARGE_MIN:
                        write_mode(MODE_CHARGER_ONLY)
                        state = STATE_CHARGING
                    else:
                        write_mode(MODE_ON)
                        state = STATE_ON

                    await_next_day = False
                    night_seen_since_shutdown = False

                time.sleep(POLL_INTERVAL_S)
                continue

            if state == STATE_CHARGING:
                # nachts und SOC < SOC_CHARGE_MIN => OFF bleiben
                if TURN_OFF_AT_NIGHT_WHEN_BELOW_CHARGE_MIN and night_detected and (m.soc_percent < SOC_CHARGE_MIN) and not pv_surplus_confirmed:
                    write_mode(MODE_OFF)
                    state = STATE_OFF
                    await_next_day = True
                    night_seen_since_shutdown = True
                    pv_surplus_since = None
                    time.sleep(POLL_INTERVAL_S)
                    continue

                # tagsüber normal: ChargerOnly halten bis SOC_CHARGE_MIN erreicht
                write_mode(MODE_CHARGER_ONLY)

                if m.soc_percent >= SOC_CHARGE_MIN:
                    write_mode(MODE_ON)
                    state = STATE_ON

                time.sleep(POLL_INTERVAL_S)
                continue

            if state == STATE_ON:
                write_mode(MODE_ON)

                if m.soc_percent <= SOC_MIN:
                    # Sequenz starten: ChargerOnly 30s, dann Off
                    write_mode(MODE_CHARGER_ONLY)
                    state = STATE_OFF_DELAY
                    off_delay_start = now

                time.sleep(POLL_INTERVAL_S)
                continue

            if state == STATE_OFF_DELAY:
                write_mode(MODE_CHARGER_ONLY)

                if off_delay_start is None:
                    off_delay_start = now

                if (now - off_delay_start) >= OFF_DELAY_SECONDS:
                    write_mode(MODE_OFF)
                    state = STATE_OFF
                    off_delay_start = None

                    await_next_day = True
                    night_seen_since_shutdown = False
                    pv_surplus_since = None

                time.sleep(POLL_INTERVAL_S)
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
