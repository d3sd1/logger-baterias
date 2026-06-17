"""Offline protocol tests — no hardware needed.

Builds a synthetic 300-byte JK-PB status frame, then checks round-trip decode
and the request-frame CRC.
"""
import struct
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jkpb import protocol as P


def make_frame(cells_mv, bat_mv, bat_ma, soc, charge_fet, discharge_fet):
    f = bytearray(P.FRAME_LEN)
    f[0:4] = P.SYNC
    for i, mv in enumerate(cells_mv):
        struct.pack_into("<H", f, P.OFF_CELL0 + i * 2, mv)
    struct.pack_into("<h", f, P.OFF_MOS_TEMP, 253)        # 25.3 C
    struct.pack_into("<I", f, P.OFF_BAT_VOLT, bat_mv)
    struct.pack_into("<i", f, P.OFF_BAT_CURR, bat_ma)
    struct.pack_into("<h", f, P.OFF_TEMP1, 210)           # 21.0 C
    f[P.OFF_BALANCE_FLAG] = 1
    f[P.OFF_SOC] = soc
    f[P.OFF_SOH] = 99
    struct.pack_into("<i", f, P.OFF_CAP_REMAIN, 180000)   # 180.0 Ah
    struct.pack_into("<i", f, P.OFF_CYCLES, 42)
    f[P.OFF_CHARGE_FET] = int(charge_fet)
    f[P.OFF_DISCHARGE_FET] = int(discharge_fet)
    f[P.FRAME_LEN - 1] = sum(f[: P.FRAME_LEN - 1]) & 0xFF  # checksum
    return bytes(f)


def test_request_crc():
    # Modbus CRC16 of a known sequence
    assert P.crc16_modbus(b"\x01\x03\x00\x05\x00\x02") == bytes.fromhex("d40a")


def test_build_request_shape():
    req = P.build_request(0x03)
    assert req[0] == 0x03
    assert req[1:9] == P.CMD_STATUS
    assert len(req) == 11
    assert P.crc16_modbus(req[:9]) == req[9:11]


def test_decode_roundtrip():
    cells = [3300 + i for i in range(16)]   # 3.300..3.315 V
    frame = make_frame(cells, bat_mv=52840, bat_ma=-12500, soc=87,
                        charge_fet=True, discharge_fet=False)
    assert P._valid_checksum(frame)
    r = P.decode_status(frame, address=3, cell_count=16)
    assert r.cell_count == 16
    assert r.cell_mv == cells
    assert r.cell_min_mv == 3300 and r.cell_max_mv == 3315
    assert r.cell_delta_mv == 15
    assert abs(r.bat_v - 52.84) < 1e-6
    assert abs(r.bat_a - (-12.5)) < 1e-6   # discharging
    assert r.soc == 87 and r.soh == 99
    assert abs(r.cap_remain_ah - 180.0) < 1e-6
    assert r.cycles == 42
    assert abs(r.mos_temp_c - 25.3) < 1e-6
    assert r.balancing is True
    assert r.charge_fet is True
    assert r.discharge_fet is False


def test_find_frame_with_preamble():
    cells = [3333] * 16
    frame = make_frame(cells, 53000, 0, 50, True, True)
    buf = b"\x00\xff\x99" + frame + b"\xAA\xBB"   # garbage + frame + trailing
    found = P.find_frame(buf)
    assert found == frame


if __name__ == "__main__":
    test_request_crc()
    test_build_request_shape()
    test_decode_roundtrip()
    test_find_frame_with_preamble()
    print("all protocol tests passed")
