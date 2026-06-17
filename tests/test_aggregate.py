"""Offline aggregate tests — no hardware."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from jkpb.protocol import PackReading
from jkpb.aggregate import aggregate


def mk(addr, v, a, soc, cmin, cmax, chg=True, dis=True, bal=False):
    return PackReading(
        address=addr, cell_mv=[cmin, cmax], bat_v=v, bat_a=a, soc=soc,
        soh=99, cap_remain_ah=100.0, cycles=10, mos_temp_c=25.0,
        temps_c=[24.0, 24.5, 0.0, 0.0], balancing=bal,
        charge_fet=chg, discharge_fet=dis, prot_bits=0,
    )


def test_single_pack_passthrough():
    agg = aggregate([mk(1, 52.8, -10.0, 80, 3290, 3310)])
    assert agg.pack_count == 1
    assert abs(agg.voltage - 52.8) < 1e-6
    assert abs(agg.current - (-10.0)) < 1e-6
    assert agg.soc == 80
    assert agg.cell_delta_mv == 20
    assert agg.allow_charge and agg.allow_discharge


def test_parallel_currents_sum_voltage_mean():
    agg = aggregate([
        mk(0, 52.8, -10.0, 80, 3300, 3310),
        mk(1, 52.6, -8.0, 78, 3280, 3320),
    ])
    assert agg.pack_count == 2
    assert abs(agg.voltage - 52.7) < 1e-6        # mean
    assert abs(agg.current - (-18.0)) < 1e-6     # sum
    assert agg.cell_min_mv == 3280               # across bank
    assert agg.cell_max_mv == 3320
    assert agg.soc == 79.0
    assert abs(agg.cap_remain_ah - 200.0) < 1e-6


def test_allow_flags_are_AND():
    # one pack with discharge FET off -> bank must not allow discharge
    agg = aggregate([
        mk(0, 52.8, 0.0, 80, 3300, 3310, dis=True),
        mk(1, 52.8, 0.0, 80, 3300, 3310, dis=False),
    ])
    assert agg.allow_discharge is False
    assert agg.allow_charge is True


if __name__ == "__main__":
    test_single_pack_passthrough()
    test_parallel_currents_sum_voltage_mean()
    test_allow_flags_are_AND()
    print("all aggregate tests passed")
