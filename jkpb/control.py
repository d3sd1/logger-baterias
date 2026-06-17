"""Per-pack MOSFET control — charge / discharge enable.

⚠️ SAFETY-CRITICAL and HARDWARE-UNVERIFIED.

The write frame below targets registers BatChargeEN (0x0070) and
BatDisChargeEN (0x0074) per the JK-PB2A16S20P Modbus spec (ciciban), via a
Modbus-RTU FC16 write. This has NOT been confirmed on real hardware. Until you
validate it against ONE pack on the bench, keep `allow_mosfet_writes: false`.

Writes are gated three ways:
  1. `allow_mosfet_writes` config flag (default false)
  2. `dry_run` / simulate mode never reaches `apply()`
  3. `PackController.armed` must be set True explicitly
"""

import struct

from . import protocol as P

REG_CHARGE_EN = 0x0070       # BatChargeEN,    UINT32, 1=on 0=off
REG_DISCHARGE_EN = 0x0074    # BatDisChargeEN, UINT32, 1=on 0=off


def build_write_u32(address: int, register: int, value: int) -> bytes:
    """Modbus-RTU FC16 (0x10) write of one UINT32 (2 regs) to `register`.

    CANDIDATE frame — verify on hardware before enabling writes.
    """
    body = struct.pack(
        ">BBHHB I",      # addr, func, start reg, qty regs, byte count, value(BE u32)
        address & 0xFF,  # unit address
        0x10,            # FC16 write multiple registers
        register & 0xFFFF,
        2,               # 2 x 16-bit registers = 1 UINT32
        4,               # byte count
        value & 0xFFFFFFFF,
    )
    return body + P.crc16_modbus(body)


class PackController:
    """Applies charge/discharge MOSFET changes to a single pack address."""

    def __init__(self, bus, allow_mosfet_writes: bool = False):
        self.bus = bus                       # an open JkPbBus
        self.allow = allow_mosfet_writes
        self.armed = False                   # must be flipped True to ever write

    def _write(self, address: int, register: int, on: bool) -> bytes:
        frame = build_write_u32(address, register, 1 if on else 0)
        if not (self.allow and self.armed):
            raise PermissionError(
                "MOSFET write blocked: allow_mosfet_writes/armed not both set"
            )
        self.bus._ser.reset_input_buffer()
        self.bus._ser.write(frame)
        self.bus._ser.flush()
        return self.bus._ser.read(64)        # ack

    def set_charge(self, address: int, on: bool):
        return self._write(address, REG_CHARGE_EN, on)

    def set_discharge(self, address: int, on: bool):
        return self._write(address, REG_DISCHARGE_EN, on)
