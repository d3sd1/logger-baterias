#!/usr/bin/env python3
"""JK-PB bank logger — poll all packs, decode every cell, store to SQLite/CSV.

READ-ONLY. No MOSFET writes here. Safe to run against the live bank.

Usage:
    python logger.py                  # uses config.yaml
    python logger.py --once           # single poll, print table, exit
    python logger.py --config foo.yaml
"""

import argparse
import sys
import time

import yaml

from jkpb.transport import JkPbBus
from jkpb.storage import Store


def load_config(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def poll_bank(bus, addresses, cell_count, retries):
    readings = []
    for addr in addresses:
        r = None
        for _ in range(retries + 1):
            r = bus.poll(addr, cell_count)
            if r is not None:
                break
            time.sleep(0.05)
        if r is None:
            print(f"  ! pack addr {addr}: no/invalid frame", file=sys.stderr)
        else:
            readings.append(r)
        time.sleep(0.12)  # COMMAND_GAP
    return readings


def print_table(readings):
    print(f"{'addr':>4} {'V':>7} {'A':>8} {'SOC':>4} {'dmV':>5} "
          f"{'min':>5} {'max':>5} {'Tmos':>5} {'bal':>3} {'chg':>3} {'dis':>3}")
    for r in readings:
        print(f"{r.address:>4} {r.bat_v:>7.2f} {r.bat_a:>8.2f} {r.soc:>4} "
              f"{r.cell_delta_mv:>5} {r.cell_min_mv:>5} {r.cell_max_mv:>5} "
              f"{r.mos_temp_c:>5.1f} {int(r.balancing):>3} "
              f"{int(r.charge_fet):>3} {int(r.discharge_fet):>3}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--once", action="store_true", help="single poll then exit")
    args = ap.parse_args()

    cfg = load_config(args.config)
    addresses = cfg["bank"]["addresses"]
    cell_count = cfg["bank"]["cell_count"]
    retries = cfg["poll"]["retries"]
    interval = cfg["poll"]["interval_s"]

    bus = JkPbBus(cfg["serial"]["port"], cfg["serial"]["baud"])
    store = None if args.once else Store(cfg["storage"]["db"], cfg["storage"].get("csv", ""))

    try:
        bus.open()
    except Exception as e:
        print(f"ERROR opening {cfg['serial']['port']}: {e}", file=sys.stderr)
        sys.exit(1)

    try:
        while True:
            t0 = time.time()
            readings = poll_bank(bus, addresses, cell_count, retries)
            if args.once:
                print_table(readings)
                break
            if readings:
                store.write(readings, ts=t0)
            ok = len(readings)
            print(f"[{time.strftime('%H:%M:%S')}] logged {ok}/{len(addresses)} packs")
            dt = interval - (time.time() - t0)
            if dt > 0:
                time.sleep(dt)
    except KeyboardInterrupt:
        print("\nstopped.")
    finally:
        bus.close()
        if store:
            store.close()


if __name__ == "__main__":
    main()
