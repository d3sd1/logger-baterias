"""Aggregate N parallel JK-PB packs into one virtual bank battery.

Packs are in PARALLEL: same nominal voltage, currents add, capacities add.
The aggregate is what the Ekrano GX sees as a single battery.
"""

from dataclasses import dataclass
from typing import List

from .protocol import PackReading


@dataclass
class BankAggregate:
    voltage: float          # V  (mean pack voltage)
    current: float          # A  (+ charge / - discharge, sum of packs)
    power: float            # W
    soc: float              # %  (mean of packs)
    cell_min_mv: int        # lowest cell across whole bank
    cell_max_mv: int        # highest cell across whole bank
    cell_delta_mv: int
    temp_max_c: float       # hottest sensor across bank (conservative)
    cap_remain_ah: float    # sum of remaining capacity
    allow_charge: bool      # AND of pack charge-FETs
    allow_discharge: bool   # AND of pack discharge-FETs
    balancing: bool         # any pack balancing
    pack_count: int


def aggregate(readings: List[PackReading]) -> BankAggregate:
    if not readings:
        raise ValueError("no pack readings to aggregate")

    n = len(readings)
    voltage = sum(r.bat_v for r in readings) / n
    current = sum(r.bat_a for r in readings)
    soc = sum(r.soc for r in readings) / n

    all_cells_min = [r.cell_min_mv for r in readings if r.cell_count]
    all_cells_max = [r.cell_max_mv for r in readings if r.cell_count]
    cmin = min(all_cells_min) if all_cells_min else 0
    cmax = max(all_cells_max) if all_cells_max else 0

    temps = [r.mos_temp_c for r in readings] + [t for r in readings for t in r.temps_c if t > -40]

    return BankAggregate(
        voltage=round(voltage, 3),
        current=round(current, 2),
        power=round(voltage * current, 1),
        soc=round(soc, 1),
        cell_min_mv=cmin,
        cell_max_mv=cmax,
        cell_delta_mv=cmax - cmin,
        temp_max_c=round(max(temps), 1) if temps else 0.0,
        cap_remain_ah=round(sum(r.cap_remain_ah for r in readings), 2),
        allow_charge=all(r.charge_fet for r in readings),
        allow_discharge=all(r.discharge_fet for r in readings),
        balancing=any(r.balancing for r in readings),
        pack_count=n,
    )
