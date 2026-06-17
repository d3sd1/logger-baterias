"""JK-PB series BMS serial protocol (frame build + decode).

Verified against the dbus-serialbattery `jkbms_pb.py` driver and the
JK-PB2A16S20P RS485 spec. NOTE: the JK-PB is NOT a plain Modbus device for
reads. The *request* is framed with a Modbus-RTU CRC16, but the *response* is a
fixed ~300-byte JK status frame (header 0x55AA 0xEB90) decoded by byte offset,
ended by a sum8 checksum.

Read-only. MOSFET write frames live in `control.py` (built separately, with
hardware validation, for the balancer).
"""

import struct
from dataclasses import dataclass, field
from typing import List, Optional

BAUD = 115200            # JK-PB default
COMMAND_GAP = 0.12       # seconds; min gap between commands on the bus
FRAME_LEN = 300          # status payload length (bytes)

# Request commands (8 bytes each). Frame on wire = addr + cmd + crc16(addr+cmd).
CMD_STATUS = bytes.fromhex("10 16 20 00 01 02 00 00".replace(" ", ""))
CMD_SETTINGS = bytes.fromhex("10 16 1e 00 01 02 00 00".replace(" ", ""))
CMD_ABOUT = bytes.fromhex("10 16 1c 00 01 02 00 00".replace(" ", ""))

SYNC = b"\x55\xaa\xeb\x90"   # status frame header

# --- absolute byte offsets into the 300-byte status frame ---
OFF_CELL0 = 6            # cell N voltage at (N*2 + 6), <H, mV
OFF_MOS_TEMP = 144       # <h, 0.1 C
OFF_BAT_VOLT = 150       # <I, mV
OFF_BAT_CURR = 158       # <i, mA (signed: + charge / - discharge)
OFF_TEMP1 = 162          # <h, 0.1 C
OFF_TEMP2 = 164          # <h, 0.1 C
OFF_PROT_BITS = 166      # <I, bitfield
OFF_BALANCE_FLAG = 172   # <B, 0/1
OFF_SOC = 173            # <B, %
OFF_CAP_REMAIN = 174     # <i, mAh
OFF_CYCLES = 182         # <i, count
OFF_SOH = 190            # <B, %
OFF_CHARGE_FET = 198     # <B, 0/1
OFF_DISCHARGE_FET = 199  # <B, 0/1
OFF_TEMP3 = 256          # <h, 0.1 C
OFF_TEMP4 = 258          # <h, 0.1 C


def crc16_modbus(data: bytes) -> bytes:
    """Modbus-RTU CRC16 (poly 0xA001), little-endian on the wire."""
    crc = 0xFFFF
    for b in data:
        crc ^= b
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return struct.pack("<H", crc)


def build_request(address: int, command: bytes = CMD_STATUS) -> bytes:
    """Build the on-wire request frame for one BMS address (DIP 0..15)."""
    head = bytes([address]) + command
    return head + crc16_modbus(head)


@dataclass
class PackReading:
    address: int
    cell_mv: List[int] = field(default_factory=list)   # per-cell mV
    bat_v: float = 0.0
    bat_a: float = 0.0          # + charge / - discharge
    soc: int = 0
    soh: int = 0
    cap_remain_ah: float = 0.0
    cycles: int = 0
    mos_temp_c: float = 0.0
    temps_c: List[float] = field(default_factory=list)
    balancing: bool = False
    charge_fet: bool = False
    discharge_fet: bool = False
    prot_bits: int = 0

    @property
    def cell_count(self) -> int:
        return len(self.cell_mv)

    @property
    def cell_min_mv(self) -> int:
        return min(self.cell_mv) if self.cell_mv else 0

    @property
    def cell_max_mv(self) -> int:
        return max(self.cell_mv) if self.cell_mv else 0

    @property
    def cell_delta_mv(self) -> int:
        return self.cell_max_mv - self.cell_min_mv if self.cell_mv else 0


def _valid_checksum(frame: bytes) -> bool:
    """Status frame ends with sum8 of bytes [0..298] at byte 299."""
    if len(frame) < FRAME_LEN:
        return False
    return (sum(frame[: FRAME_LEN - 1]) & 0xFF) == frame[FRAME_LEN - 1]


def find_frame(buf: bytes) -> Optional[bytes]:
    """Locate one full 300-byte status frame inside a raw read buffer."""
    idx = buf.find(SYNC)
    if idx < 0 or len(buf) - idx < FRAME_LEN:
        return None
    return buf[idx: idx + FRAME_LEN]


def decode_status(frame: bytes, address: int, cell_count: int = 16) -> PackReading:
    """Decode a validated 300-byte status frame into a PackReading."""
    def u16(o): return struct.unpack_from("<H", frame, o)[0]
    def s16(o): return struct.unpack_from("<h", frame, o)[0]
    def u32(o): return struct.unpack_from("<I", frame, o)[0]
    def s32(o): return struct.unpack_from("<i", frame, o)[0]
    def u8(o): return frame[o]

    cells = [u16(OFF_CELL0 + c * 2) for c in range(cell_count)]
    # trim trailing zero cells (unused channels read 0)
    while cells and cells[-1] == 0:
        cells.pop()

    return PackReading(
        address=address,
        cell_mv=cells,
        bat_v=u32(OFF_BAT_VOLT) / 1000.0,
        bat_a=s32(OFF_BAT_CURR) / 1000.0,
        soc=u8(OFF_SOC),
        soh=u8(OFF_SOH),
        cap_remain_ah=s32(OFF_CAP_REMAIN) / 1000.0,
        cycles=s32(OFF_CYCLES),
        mos_temp_c=s16(OFF_MOS_TEMP) / 10.0,
        temps_c=[s16(o) / 10.0 for o in (OFF_TEMP1, OFF_TEMP2, OFF_TEMP3, OFF_TEMP4)],
        balancing=bool(u8(OFF_BALANCE_FLAG)),
        charge_fet=bool(u8(OFF_CHARGE_FET)),
        discharge_fet=bool(u8(OFF_DISCHARGE_FET)),
        prot_bits=u32(OFF_PROT_BITS),
    )
