"""Storage: SQLite (queryable for papers) + optional per-poll CSV.

Schema is wide-but-tidy: one row per (timestamp, pack), cells as JSON so any
cell count works and the table stays analysis-friendly.
"""

import csv
import json
import os
import sqlite3
import time
from typing import List

from .protocol import PackReading

SCHEMA = """
CREATE TABLE IF NOT EXISTS reading (
    ts            REAL    NOT NULL,
    address       INTEGER NOT NULL,
    bat_v         REAL,
    bat_a         REAL,
    soc           INTEGER,
    soh           INTEGER,
    cap_remain_ah REAL,
    cycles        INTEGER,
    mos_temp_c    REAL,
    cell_min_mv   INTEGER,
    cell_max_mv   INTEGER,
    cell_delta_mv INTEGER,
    balancing     INTEGER,
    charge_fet    INTEGER,
    discharge_fet INTEGER,
    prot_bits     INTEGER,
    cells_mv      TEXT,   -- JSON array of per-cell mV
    temps_c       TEXT    -- JSON array of sensor temps
);
CREATE INDEX IF NOT EXISTS idx_reading_ts ON reading(ts);
CREATE INDEX IF NOT EXISTS idx_reading_addr_ts ON reading(address, ts);
"""


class Store:
    def __init__(self, db_path: str, csv_path: str = ""):
        self.db_path = db_path
        self.csv_path = csv_path
        os.makedirs(os.path.dirname(os.path.abspath(db_path)) or ".", exist_ok=True)
        self.conn = sqlite3.connect(db_path)
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        self._csv_inited = os.path.exists(csv_path) if csv_path else True

    def write(self, readings: List[PackReading], ts: float = None):
        ts = ts if ts is not None else time.time()
        rows = []
        for r in readings:
            rows.append((
                ts, r.address, r.bat_v, r.bat_a, r.soc, r.soh,
                r.cap_remain_ah, r.cycles, r.mos_temp_c,
                r.cell_min_mv, r.cell_max_mv, r.cell_delta_mv,
                int(r.balancing), int(r.charge_fet), int(r.discharge_fet),
                r.prot_bits, json.dumps(r.cell_mv), json.dumps(r.temps_c),
            ))
        self.conn.executemany(
            "INSERT INTO reading VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", rows
        )
        self.conn.commit()
        if self.csv_path:
            self._write_csv(rows)

    def _write_csv(self, rows):
        header = [
            "ts", "address", "bat_v", "bat_a", "soc", "soh", "cap_remain_ah",
            "cycles", "mos_temp_c", "cell_min_mv", "cell_max_mv", "cell_delta_mv",
            "balancing", "charge_fet", "discharge_fet", "prot_bits",
            "cells_mv", "temps_c",
        ]
        new = not self._csv_inited
        with open(self.csv_path, "a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(header)
                self._csv_inited = True
            w.writerows(rows)

    def close(self):
        self.conn.close()
