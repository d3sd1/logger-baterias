"""Offline balancer tests — no hardware."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jkpb.protocol import PackReading
from jkpb.balancer import Balancer, BalancerConfig


def mk(addr, cmin, cmax, a=2.0, chg=True, dis=True):
    return PackReading(address=addr, cell_mv=[cmin, cmax], bat_v=53.0, bat_a=a,
                       soc=85, soh=99, cap_remain_ah=170.0, cycles=10,
                       mos_temp_c=25.0, temps_c=[24.0], balancing=False,
                       charge_fet=chg, discharge_fet=dis, prot_bits=0)


def cfg(**kw):
    base = dict(enabled=True, isolate_delta_mv=60, recover_delta_mv=20,
                max_switch_current_a=5.0, max_isolated_packs=1, min_cell_mv=2900)
    base.update(kw)
    return BalancerConfig(**base)


def test_isolates_imbalanced_pack():
    b = Balancer(cfg())
    rd = [mk(0, 3300, 3320), mk(1, 3300, 3320), mk(2, 3300, 3320), mk(3, 3310, 3420)]
    acts = b.evaluate(rd, bank_current_a=2.0)
    assert len(acts) == 1
    assert acts[0].address == 3
    assert acts[0].set_charge is True and acts[0].set_discharge is False
    assert b.isolated[3] is True


def test_no_switch_under_load():
    b = Balancer(cfg())
    rd = [mk(0, 3300, 3320), mk(3, 3310, 3420)]
    acts = b.evaluate(rd, bank_current_a=50.0)   # high current
    assert acts == []


def test_recover_when_settled():
    b = Balancer(cfg())
    rd = [mk(0, 3300, 3320), mk(1, 3300, 3320), mk(2, 3300, 3320), mk(3, 3310, 3420)]
    b.evaluate(rd, 2.0)                 # isolates pack 3
    assert b.isolated[3] is True
    rd2 = [mk(0, 3300, 3340), mk(1, 3300, 3340), mk(2, 3300, 3340), mk(3, 3300, 3345)]
    acts = b.evaluate(rd2, 2.0)         # pack 3 now in line -> recover
    assert any(x.address == 3 and x.set_discharge for x in acts)
    assert b.isolated[3] is False


def test_max_isolated_cap():
    b = Balancer(cfg(max_isolated_packs=1))
    rd = [mk(0, 3300, 3300), mk(1, 3300, 3300), mk(2, 3310, 3450), mk(3, 3310, 3445)]
    acts = b.evaluate(rd, 2.0)
    assert len(acts) == 1   # two candidates over threshold, but cap = 1


def test_never_isolate_lowest():
    b = Balancer(cfg(isolate_delta_mv=5))
    # pack 3 has highest top cell AND lowest min cell -> must not be isolated
    rd = [mk(0, 3300, 3320), mk(1, 3300, 3320), mk(3, 2950, 3420)]
    acts = b.evaluate(rd, 2.0)
    assert all(x.address != 3 for x in acts)


def test_disabled_recovers_nothing_left_isolated():
    b = Balancer(cfg(enabled=True))
    rd = [mk(0, 3300, 3320), mk(1, 3300, 3320), mk(2, 3300, 3320), mk(3, 3310, 3420)]
    b.evaluate(rd, 2.0)
    b.cfg.enabled = False
    acts = b.evaluate(rd, 2.0)
    assert any(x.address == 3 and x.set_discharge for x in acts)


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
    print("all balancer tests passed")
