"""Simulated bus — drop-in for JkPbBus, no hardware.

Generates synthetic PackReadings so the full pipeline (poll -> log ->
aggregate -> balancer -> publish) can be exercised in dry-run. One pack is
deliberately imbalanced (high top cell) so the balancer demonstrates an
isolate decision.

Deterministic (no RNG) so dry-run output is reproducible.
"""

import time
from typing import List, Optional

from .protocol import PackReading


class SimBus:
    def __init__(self, addresses: List[int], cell_count: int = 16,
                 imbalanced_addr: Optional[int] = None, bank_current_a: float = 2.0):
        self.addresses = addresses
        self.cell_count = cell_count
        # default: make the last address the imbalanced one
        self.imbalanced_addr = (imbalanced_addr if imbalanced_addr is not None
                                else addresses[-1])
        self.bank_current_a = bank_current_a
        self._tick = 0

    def open(self): pass
    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): self.close()

    def poll(self, address: int, cell_count: int = 16) -> Optional[PackReading]:
        cc = cell_count or self.cell_count
        base = 3320  # mV nominal per cell
        cells = [base + (i % 3) for i in range(cc)]  # tiny natural spread
        imbalanced = (address == self.imbalanced_addr)
        if imbalanced:
            # this pack has one hot cell ~ +90mV over the bank -> triggers isolate
            cells[0] = base + 95
            cells[5] = base + 40

        per_pack_current = self.bank_current_a / max(1, len(self.addresses))
        pack_v = sum(cells) / 1000.0

        return PackReading(
            address=address,
            cell_mv=cells,
            bat_v=round(pack_v, 3),
            bat_a=round(per_pack_current, 2),   # + charging
            soc=85 if not imbalanced else 90,
            soh=99,
            cap_remain_ah=170.0,
            cycles=12,
            mos_temp_c=26.5,
            temps_c=[24.0, 24.5, 0.0, 0.0],
            balancing=imbalanced,
            charge_fet=True,
            discharge_fet=True,
            prot_bits=0,
        )

    def poll_all(self, addresses, cell_count: int = 16):
        for addr in addresses:
            yield addr, self.poll(addr, cell_count)
