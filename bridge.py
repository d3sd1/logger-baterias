#!/usr/bin/env python3
"""Bridge: poll bank -> log -> aggregate -> balancer -> publish to Ekrano GX.

Modes:
  python bridge.py                 LIVE. Real serial + MQTT. MOSFET writes only
                                   if balancer.enabled AND balancer.allow_mosfet_writes.
  python bridge.py --dry-run       Real serial read, but NO MQTT publish and
                                   NO MOSFET writes. Prints what it WOULD do.
  python bridge.py --simulate      No hardware at all. Synthetic packs (one
                                   imbalanced). NO MQTT, NO writes. Full pipeline
                                   printed so you see exactly what it would do.

In --simulate and --dry-run nothing is ever written to a BMS or published.
"""

import argparse
import json
import sys
import time

import yaml

from jkpb.storage import Store
from jkpb.aggregate import aggregate
from jkpb.balancer import Balancer, BalancerConfig
from logger import poll_bank


def make_bus(cfg, simulate):
    if simulate:
        from jkpb.sim import SimBus
        scfg = cfg.get("sim", {})
        addrs = scfg.get("addresses", [0, 1, 2, 3])
        return SimBus(addrs, cfg["bank"]["cell_count"],
                      imbalanced_addr=scfg.get("imbalanced_addr"),
                      bank_current_a=scfg.get("bank_current_a", 2.0)), addrs
    from jkpb.transport import JkPbBus
    bus = JkPbBus(cfg["serial"]["port"], cfg["serial"]["baud"])
    return bus, cfg["bank"]["addresses"]


def build_payload_preview(agg, limits):
    """Same payload the MQTT publisher would send (for dry/sim printing)."""
    return {
        "Dc": {"Power": agg.power, "Voltage": agg.voltage,
               "Current": agg.current, "Temperature": agg.temp_max_c},
        "Soc": agg.soc, "Capacity": agg.cap_remain_ah,
        "Balancing": int(agg.balancing),
        "System": {"MinCellVoltage": agg.cell_min_mv / 1000.0,
                   "MaxCellVoltage": agg.cell_max_mv / 1000.0,
                   "NrOfModulesOnline": agg.pack_count},
        "Info": {"MaxChargeVoltage": limits["max_charge_voltage"],
                 "MaxChargeCurrent": limits["max_charge_current"],
                 "MaxDischargeCurrent": limits["max_discharge_current"]},
        "Io": {"AllowToCharge": int(agg.allow_charge),
               "AllowToDischarge": int(agg.allow_discharge)},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--dry-run", action="store_true",
                    help="real serial read, no MQTT, no MOSFET writes")
    ap.add_argument("--simulate", action="store_true",
                    help="no hardware; synthetic packs; no MQTT; no writes")
    ap.add_argument("--once", action="store_true", help="single cycle then exit")
    ap.add_argument("--enable-balancer", action="store_true",
                    help="force balancer logic on (still no writes unless LIVE+allow_mosfet_writes)")
    args = ap.parse_args()

    with open(args.config, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    side_effects = not (args.dry_run or args.simulate)   # True only in LIVE
    cells = cfg["bank"]["cell_count"]
    retries = cfg["poll"]["retries"]
    interval = cfg["poll"]["interval_s"]
    limits = cfg["limits"]

    bcfg = cfg.get("balancer", {})
    balancer = Balancer(BalancerConfig(
        enabled=bcfg.get("enabled", False) or args.enable_balancer,
        isolate_delta_mv=bcfg.get("isolate_delta_mv", 60),
        recover_delta_mv=bcfg.get("recover_delta_mv", 20),
        max_switch_current_a=bcfg.get("max_switch_current_a", 5.0),
        max_isolated_packs=bcfg.get("max_isolated_packs", 1),
        min_cell_mv=bcfg.get("min_cell_mv", 2900),
    ))

    bus, addrs = make_bus(cfg, args.simulate)
    store = Store(cfg["storage"]["db"], cfg["storage"].get("csv", "")) if side_effects else None

    pub = None
    controller = None
    if side_effects:
        from jkpb.mqtt_publisher import VenusBatteryPublisher
        from jkpb.control import PackController
        m = cfg["mqtt"]
        pub = VenusBatteryPublisher(host=m["host"], vrm_id=m["vrm_id"],
                                    instance=m.get("instance", 1), port=m.get("port", 1883),
                                    username=m.get("username", ""), password=m.get("password", ""))
        pub.connect()
        controller = PackController(bus, allow_mosfet_writes=bcfg.get("allow_mosfet_writes", False))
        controller.armed = bcfg.get("allow_mosfet_writes", False)

    mode = "SIMULATE" if args.simulate else "DRY-RUN" if args.dry_run else "LIVE"
    print(f"=== bridge mode: {mode} ===")
    if not side_effects:
        print("    (no MQTT publish, no MOSFET writes - preview only)\n")

    try:
        bus.open()
    except Exception as e:
        print(f"ERROR opening bus: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        while True:
            t0 = time.time()
            readings = poll_bank(bus, addrs, cells, retries)
            if not readings:
                print(f"[{time.strftime('%H:%M:%S')}] no packs answered", file=sys.stderr)
                if args.once:
                    break
                time.sleep(interval); continue

            if store:
                store.write(readings, ts=t0)

            # per-pack table
            print(f"[{time.strftime('%H:%M:%S')}] packs:")
            for r in readings:
                print(f"   addr {r.address:>2}  {r.bat_v:6.2f}V {r.bat_a:+6.1f}A "
                      f"SOC {r.soc:>3}  cell {r.cell_min_mv}-{r.cell_max_mv} "
                      f"(d{r.cell_delta_mv})  bal={int(r.balancing)} "
                      f"chg={int(r.charge_fet)} dis={int(r.discharge_fet)}")

            agg = aggregate(readings)
            print(f"   BANK: {agg.pack_count}p {agg.voltage:.2f}V {agg.current:+.1f}A "
                  f"SOC {agg.soc:.0f}%  cell {agg.cell_min_mv}-{agg.cell_max_mv} "
                  f"(d{agg.cell_delta_mv})")

            # balancer decisions
            actions = balancer.evaluate(readings, agg.current)
            if actions:
                for a in actions:
                    if side_effects and controller:
                        try:
                            controller.set_charge(a.address, a.set_charge)
                            controller.set_discharge(a.address, a.set_discharge)
                            print(f"   APPLIED: {a}")
                        except PermissionError as e:
                            print(f"   BLOCKED ({e}): {a}")
                    else:
                        print(f"   WOULD: {a}")
            else:
                print("   balancer: no action")

            # mqtt
            payload = build_payload_preview(agg, limits)
            if side_effects and pub:
                pub.publish(agg, limits)
                print(f"   MQTT published -> {pub.topic}")
            else:
                print(f"   WOULD PUBLISH MQTT: {json.dumps(payload)}")

            print()
            if args.once:
                break
            dt = interval - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        bus.close()
        if store:
            store.close()
        if pub:
            pub.disconnect()


if __name__ == "__main__":
    main()
