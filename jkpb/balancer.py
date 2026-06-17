"""Smart per-pack balancer.

Goal: if ONE pack is overcharged relative to the bank (its top cell runs high
and it drags the others), put THAT pack into charge-only (discharge MOSFET OFF,
charge MOSFET ON). Its internal JK 2A active balancer then equalises its cells
while the bank keeps working. Auto-recover to normal when it settles.

This module only DECIDES. It emits Actions. Applying them (real MOSFET writes)
is done by PackController and is gated off by default. In dry-run/simulate the
actions are printed, never applied.

SAFETY GATES (all must pass before an isolate action is emitted):
  - bank current magnitude below `max_switch_current_a` (don't switch under load)
  - at most `max_isolated_packs` isolated at once
  - never isolate the LOWEST pack (it needs to charge)
  - hysteresis: isolate above `isolate_delta_mv`, recover below `recover_delta_mv`
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from .protocol import PackReading


@dataclass
class Action:
    address: int
    set_charge: bool
    set_discharge: bool
    reason: str

    def __str__(self):
        mode = "CHARGE-ONLY" if (self.set_charge and not self.set_discharge) else \
               "NORMAL" if (self.set_charge and self.set_discharge) else \
               f"chg={int(self.set_charge)} dis={int(self.set_discharge)}"
        return f"pack {self.address:>2} -> {mode}  ({self.reason})"


@dataclass
class BalancerConfig:
    enabled: bool = False
    isolate_delta_mv: int = 60        # pack max-cell above bank avg-of-maxes by this -> candidate
    recover_delta_mv: int = 20        # below this -> return to normal
    max_switch_current_a: float = 5.0 # only switch MOSFET when |bank current| under this
    max_isolated_packs: int = 1       # never isolate more than this at once
    min_cell_mv: int = 2900           # never isolate a pack whose min cell is this low


class Balancer:
    def __init__(self, cfg: BalancerConfig):
        self.cfg = cfg
        self.isolated: Dict[int, bool] = {}   # address -> True if currently charge-only

    def evaluate(self, readings: List[PackReading], bank_current_a: float) -> List[Action]:
        """Return the set of state-changing actions for this cycle (may be empty)."""
        cfg = self.cfg
        actions: List[Action] = []
        if not cfg.enabled or len(readings) < 2:
            # with <2 packs there is nothing to balance against
            return self._recover_all(readings) if not cfg.enabled else []

        by_addr = {r.address: r for r in readings}
        # reference: how high each pack's TOP cell sits vs the bank
        max_cells = {r.address: r.cell_max_mv for r in readings}
        bank_avg_topcell = sum(max_cells.values()) / len(max_cells)
        lowest_pack = min(readings, key=lambda r: r.cell_min_mv).address

        can_switch = abs(bank_current_a) <= cfg.max_switch_current_a
        currently_isolated = [a for a, v in self.isolated.items() if v]

        for r in readings:
            over = max_cells[r.address] - bank_avg_topcell
            is_iso = self.isolated.get(r.address, False)

            if is_iso:
                # recover when it has settled back toward the pack
                if over <= cfg.recover_delta_mv:
                    if can_switch:
                        actions.append(Action(r.address, True, True,
                                              f"settled (+{over:.0f}mV) -> normal"))
                        self.isolated[r.address] = False
                continue

            # candidate to isolate?
            if over >= cfg.isolate_delta_mv:
                if r.address == lowest_pack:
                    continue  # don't starve the lowest pack
                if r.cell_min_mv <= cfg.min_cell_mv:
                    continue  # pack too low overall to safely stop discharge
                if len(currently_isolated) >= cfg.max_isolated_packs:
                    continue  # already at isolation cap
                if not can_switch:
                    continue  # under load -> wait
                actions.append(Action(r.address, True, False,
                                      f"top cell +{over:.0f}mV over bank -> charge-only"))
                self.isolated[r.address] = True
                currently_isolated.append(r.address)

        return actions

    def _recover_all(self, readings: List[PackReading]) -> List[Action]:
        """Balancer disabled -> ensure nothing stays isolated by us."""
        out = []
        for a, v in list(self.isolated.items()):
            if v:
                out.append(Action(a, True, True, "balancer disabled -> normal"))
                self.isolated[a] = False
        return out
