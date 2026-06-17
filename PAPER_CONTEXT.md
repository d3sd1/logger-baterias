# Research Paper Context — Per-Cell Telemetry and Per-Pack Charge-Only Balancing of a 16× Parallel JK-PB LiFePO₄ Battery Bank

> Self-contained briefing to generate a research paper. Everything needed —
> motivation, system, methodology, data, the novel algorithm, verified facts
> with sources, limitations, and reproducibility — is below.

---

## 1. One-line summary

We instrument a bank of sixteen JK-PB (JK-PB2A16S20P-class) 16S LiFePO₄ battery
packs wired in parallel, log full per-cell telemetry from every pack via a single
external Raspberry Pi over RS485, and introduce a **supervisory per-pack
charge-only balancing strategy**: when one pack's top cell runs high and drags
the bank, that single pack's discharge MOSFET is opened (charge MOSFET kept
closed) so its internal active balancer equalises it while the other fifteen
packs keep operating normally.

## 2. Motivation / problem statement

- Parallel LiFePO₄ banks are normally treated as a single aggregate by the
  inverter/charger (e.g. via a CAN-bus BMS protocol to a Victron GX). The GX
  receives only **bank-level aggregates** (total V, I, SOC, min/max cell) — not
  the individual cells of each pack. This hides intra-pack and inter-pack drift.
- Commercial BMS active balancers operate **within a pack** (cell-to-cell). They
  do not correct **inter-pack** imbalance, where one weaker/fuller pack
  repeatedly hits protection thresholds first and limits the whole bank.
- A pack whose cells diverge tends to "pull" the bank: it reaches charge cutoff
  early (capping bank charge) or discharge cutoff early (capping bank discharge).
- **Gap:** there is no off-the-shelf supervisory layer that (a) records every
  cell of every pack at scale for analysis, and (b) selectively pauses discharge
  of just the offending pack to let it self-balance without removing it from the
  bank.

## 3. Research questions / contributions

- **RQ1 — Observability.** Can a single external controller capture
  synchronized full per-cell telemetry (16 cells × 16 packs = 256 cells) plus
  per-pack current/SOC/temperature/balance state at a useful cadence, using only
  the BMS's existing serial interface?
- **RQ2 — Inter-pack divergence.** How do parallel packs diverge over
  charge/discharge cycles, and which pack-level signals (top-cell offset,
  per-cell delta, balance current) predict a pack becoming the bank-limiting one?
- **RQ3 — Supervisory balancing (the novel contribution).** Does selectively
  putting the offending pack into **charge-only** mode (discharge MOSFET off,
  charge MOSFET on) reduce inter-pack top-cell spread faster/safer than relying
  on per-pack active balancers alone, without harming bank availability?
- **C1.** An open, reproducible data-acquisition pipeline (single RS485 master,
  Raspberry Pi) for full per-cell logging of a multi-pack JK-PB bank.
- **C2.** A characterization dataset of inter-pack divergence in a real 16-pack
  bank (for the paper's empirical section).
- **C3.** A safety-gated supervisory balancing algorithm + its evaluation.

## 4. System architecture (as built)

```
16× JK-PB 16S LiFePO₄  (each: internal 2A active balancer; DIP address 0..15)
        │  shared RS485 multidrop bus (A/B/GND), 115200 8N1
        ▼
   1× isolated USB→RS485 adapter
        ▼
   Raspberry Pi  (SOLE bus master)
     • polls every pack address, decodes full per-cell frame
     • logs to SQLite + CSV  (research dataset)
     • aggregates packs -> one virtual bank battery
     • supervisory balancer: per-pack charge/discharge MOSFET decisions
        ▼  LAN / MQTT (dbus-mqtt-battery)
   Victron Ekrano GX (Venus OS)
     • sees one aggregate battery; DVCC applies CCL/DCL to inverter/charger
```

Design choice: the Raspberry Pi is the **single master** on the RS485 bus,
eliminating contention with the BMS's own inter-pack link, and feeds the GX a
single aggregated battery over MQTT. This is what makes 256-cell logging +
per-pack control possible from one cheap controller.

## 5. Data acquisition methodology

- **Transport.** RS485 multidrop; each pack addressed 0–15 via DIP switch. One
  poll per address per cycle, honoring a 120 ms inter-command gap. Default
  full-bank cadence configurable (e.g. 5 s).
- **Protocol.** JK-PB status request is a Modbus-CRC-framed command
  (`10 16 20 00 01 02 00 00`, prefixed by the 1-byte address, suffixed by a
  Modbus-RTU CRC16). The reply is a fixed ~300-byte JK status frame (header
  `55 AA EB 90`, terminated by an 8-bit checksum) decoded by byte offset — i.e.
  **not** plain Modbus register reads for telemetry.
- **Decoded per pack, per cycle:** all 16 cell voltages (mV); pack voltage (mV),
  pack current (mA, signed: + charge / − discharge); SOC (%); SOH (%); remaining
  capacity (Ah); cycle count; MOSFET temperature + up to 4 temperature sensors
  (0.1 °C); balance flag; charge-MOSFET and discharge-MOSFET status flags;
  protection bitfield.
- **Storage schema (SQLite `reading` table), one row per (timestamp, pack):**
  `ts, address, bat_v, bat_a, soc, soh, cap_remain_ah, cycles, mos_temp_c,`
  `cell_min_mv, cell_max_mv, cell_delta_mv, balancing, charge_fet,`
  `discharge_fet, prot_bits, cells_mv (JSON array), temps_c (JSON array)`.
  Indexed on `ts` and `(address, ts)`. CSV mirror for quick import to
  pandas/R/MATLAB.
- **Derived bank aggregate (parallel packs):** voltage = mean of pack voltages;
  current = sum of pack currents; capacity = sum of remaining Ah; SOC = mean;
  bank cell min/max = extremes across all 256 cells; temperature = max sensor
  (conservative); AllowToCharge/Discharge = logical AND of pack MOSFET flags.

## 6. The novel algorithm — supervisory per-pack charge-only balancing

**Intuition.** The internal balancer fixes cells *within* a pack. To fix
*between* packs without disconnecting anyone, temporarily stop the high pack from
discharging: it stops feeding the load, the other packs carry the load, and
since the high pack keeps accepting charge, its own balancer pulls its cells
together. Once it rejoins the pack envelope, restore normal mode.

**Inputs each cycle:** every pack's max cell voltage, min cell voltage, bank
current.

**Reference signal:** `over_i = max_cell_i − mean_j(max_cell_j)` — how far pack
i's top cell sits above the average pack top-cell across the bank.

**Decision (with hysteresis):**
- Isolate (charge-only) pack i when `over_i ≥ isolate_delta_mv` (default 60 mV).
- Recover (normal) an isolated pack when `over_i ≤ recover_delta_mv` (default
  20 mV).

**Safety gates (all must hold to act):**
1. **No switching under load:** act only when `|bank current| ≤ max_switch_current_a`
   (default 5 A) — avoids opening a MOSFET while it carries high current.
2. **Isolation cap:** never more than `max_isolated_packs` isolated at once
   (default 1) — keeps remaining packs from overload.
3. **Never starve the lowest pack:** the pack with the lowest min cell is never
   put charge-only (it must keep charging).
4. **Floor:** never stop discharge on a pack whose min cell ≤ `min_cell_mv`
   (default 2900 mV).
5. **Fail-safe:** disabling the supervisor returns every pack it touched to
   normal.

**Control actuation:** per-pack charge/discharge MOSFET enable via the JK-PB
registers `BatChargeEN` (0x0070) and `BatDisChargeEN` (0x0074), written 1/0
(Modbus FC16). **This write frame is not yet hardware-verified** (see §9) and is
triple-gated off by default; all reported algorithm behavior to date is from a
deterministic simulator, not live actuation.

## 7. Evaluation plan (for the paper's results section)

- **Baseline:** stock configuration — internal per-pack active balancers only,
  no supervisor. Log inter-pack top-cell spread over N full cycles.
- **Treatment:** supervisor enabled. Same cycles, same load profile.
- **Metrics:** (a) max inter-pack top-cell spread vs time; (b) time-to-converge
  below a target spread; (c) usable bank capacity (Ah delivered before first
  pack hits cutoff); (d) number/duration of isolation events; (e) energy not
  delivered during isolation (availability cost); (f) per-cell delta evolution
  inside the isolated pack (does charge-only actually accelerate its internal
  balancing?).
- **Statistics:** paired comparison across matched cycles; report effect size
  and variability, not just means.
- **Ablations:** sweep `isolate_delta_mv`, `max_switch_current_a`,
  `max_isolated_packs`.

## 8. Verified technical facts (with sources)

Each was adversarially verified (independent verifier votes). Use these as the
paper's grounding for the platform/protocol claims.

| Fact | Source | Confidence |
|---|---|---|
| JK-PB2A16S20P has two separate RS485 interfaces (RS485-1 shared w/ CAN; RS485-2 = parallel/PC, 2 RJ45). Default 115200. DIP assigns address 0–15 to poll multiple packs. | gobelpower JK-PB2A16S20P spec PDF | 3-0 |
| Inverter comms (V/T/SOC) use CAN or RS485-1; RS485-2 links packs and talks to a PC/upper computer. | shop.jkbms.com parallel guide | 3-0 |
| Parallel addressing via 4-bit DIP switch, addresses 0–15; each pack must be unique. | gobelpower spec PDF | 3-0 |
| JK-PB serial is Modbus-style over RS485, 115200 8N1, FC 03H read / 10H write, CRC framed. | github.com/ciciban/jkbms-PB2A16S20P (BMS.RS485.Modbus.V1.0) | 3-0 |
| Charge/discharge MOSFETs independently controllable: `BatChargeEN` 0x0070 and `BatDisChargeEN` 0x0074 (UINT32, 1/0, RW). | ciciban register map | 2-1 (verify on HW) |
| Per-cell voltages (UINT16 mV), pack V/I, SOC, balance current, cycle count, cell wire resistance, MOSFET temp are exposed registers. | ciciban register map | 3-0 |
| MOSFET status reported as flags / alarm bits (AlarmChargeMOS BIT16, AlarmDischargeMOS BIT17). | ciciban register map | 3-0 |
| dbus-serialbattery lists "JKBMS PB Model (serial)" and can poll multiple packs on one RS485 adapter via `BATTERY_ADDRESSES`. | mr-manuel dbus-serialbattery docs | 3-0 |
| dbus-serialbattery does **not** support charge/discharge MOSFET control for JK/JK-PB (only Daly/Seplos) — motivates our custom controller. | mr-manuel dbus-serialbattery docs | 3-0 |
| Venus OS battery-over-MQTT (`dbus-mqtt-battery`) topic `N/<VRM_ID>/battery/<inst>/JsonData`, with `Dc/Soc/Info(MaxChargeVoltage,MaxChargeCurrent,MaxDischargeCurrent)/Io(AllowToCharge,AllowToDischarge)`. | mr-manuel dbus-mqtt-battery README | primary doc |

Ground-truth decode offsets were taken from the field-tested
`dbus-serialbattery/bms/jkbms_pb.py` driver (per-cell at `2*n+6`, pack V@150,
pack I@158, SOC@173, MOSFET temp@144, charge-FET@198, discharge-FET@199, etc.).

## 9. Limitations / threats to validity

- **MOSFET write frame unverified.** The 0x0070/0x0074 write path is from the
  register-map source (single source, 2-1 vote) and has not been confirmed on
  hardware. All control results so far are simulated. Must be bench-validated on
  one pack before any live actuation or live claims.
- **Electrical risk of charge-only in a parallel bank.** Opening one pack's
  discharge MOSFET shifts its share of discharge current onto the remaining
  packs (higher per-pack current); switching a MOSFET under load can damage it.
  Hence the under-load and isolation-cap gates. Current-sharing and switching
  transients should be measured, not assumed. (Research on the safety angle was
  inconclusive — open question.)
- **Single point of failure.** Routing battery data to the GX through the Pi
  makes the Pi critical; DVCC must be configured to stop the inverter on comms
  loss (watchdog). Affects deployment claims.
- **SOC aggregation** uses a simple mean across packs (no installed-capacity
  weighting yet) — acceptable for near-identical packs, a caveat otherwise.
- **Bus mastering assumption.** We assume the Pi can be the sole RS485 master on
  the parallel bus after rewiring; if a pack insists on mastering, a dedicated
  bus/port is required. To be confirmed on the physical install.

## 10. Reproducibility

- Open pipeline (Python): `jkpb/` (protocol decode, transport, storage,
  aggregate, balancer, MOSFET control, simulator), `logger.py` (read-only
  logger), `bridge.py` (full pipeline w/ `--simulate`, `--dry-run`, live).
- Deterministic simulator (`jkpb/sim.py`) reproduces the full
  poll→log→aggregate→balance→publish path with one injected imbalanced pack — no
  hardware needed, so reviewers can rerun the algorithm logic.
- Offline unit tests: protocol round-trip, aggregation, balancer decisions
  (`tests/`).
- Hardware: Raspberry Pi + one isolated USB-RS485 adapter; 16× JK-PB on a shared
  RS485 bus, DIP-addressed 0–15; Victron Ekrano GX (Venus OS) for the aggregate
  battery + DVCC.

## 11. Suggested paper structure

1. Introduction & motivation (inter-pack imbalance in parallel LFP banks)
2. Related work (BMS active balancing, parallel-bank current sharing, Venus OS
   integrations, prior JK-BMS reverse-engineering)
3. System & data-acquisition architecture (§4–5)
4. Inter-pack divergence characterization (RQ2, dataset from §5)
5. Supervisory charge-only balancing algorithm (§6)
6. Experimental setup & metrics (§7)
7. Results (baseline vs supervised; ablations)
8. Safety analysis & limitations (§9)
9. Conclusion & future work

## 12. Candidate titles

- "Supervisory Per-Pack Charge-Only Balancing for Parallel LiFePO₄ Banks Using
  Commodity BMS Telemetry"
- "Beyond the Aggregate: Full Per-Cell Observability and Inter-Pack Balancing of
  a 16-Pack JK-BMS LiFePO₄ Array"
- "A Raspberry-Pi Supervisor for Inter-Pack Balance in Parallel Battery Systems"
