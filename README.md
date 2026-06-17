# logger-baterias — JK-PB BMS bank logger

Lee **todas las celdas y datos** de un banco de hasta 16× JK-PB (16S LiFePO4)
desde una Raspberry Pi por **un solo bus RS485**, y los guarda en SQLite + CSV
para análisis (papers).

**Estado:** Fase 1 — logger READ-ONLY. Sin escritura de MOSFET. Seguro contra el banco vivo.

## Arquitectura

```
16× JK-PB (DIP addr 0..15) ──RS485-2 multidrop──► USB-RS485 aislado ──► Raspberry Pi
                                                                          │ logger.py (este repo)
                                                                          └─(futuro) MQTT → Ekrano GX
```

La RPi es el **único master** del bus. Pollea cada dirección, decodifica el
frame de estado JK-PB (300 bytes, header `55 AA EB 90`), guarda por pack.

## Protocolo (verificado)

- Serial 115200 8N1, gap 120 ms entre comandos.
- Request = `addr + cmd_status + CRC16-Modbus`. `cmd_status = 10 16 20 00 01 02 00 00`.
- Response = 300 bytes, decode por offset de byte (ver `jkpb/protocol.py`).
- Fuente: driver `jkbms_pb.py` de dbus-serialbattery + spec JK-PB2A16S20P.

## Instalación (en la RPi)

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
```

## Uso

```bash
python logger.py --once     # una pasada, imprime tabla (test de conexión)
python logger.py            # loop continuo → data/bms.sqlite + data/bms.csv
```

Edita `config.yaml`: puerto serie, direcciones DIP de los packs, periodo.

## Tests (sin hardware)

```bash
python tests/test_protocol.py
```

## Datos guardados (por pack, por poll)

V/A banco, SOC, SOH, capacidad restante, ciclos, temp MOSFET + 4 sensores,
celda min/max/delta, flags balanceo / charge-FET / discharge-FET, bits de
protección, y **el array completo de mV por celda** (`cells_mv`, JSON).

## bridge.py — pipeline completo (lectura + MQTT + balanceador)

```bash
# SIMULACIÓN — sin hardware, sin MQTT, sin escrituras. Muestra qué haría.
python bridge.py --simulate --enable-balancer --once     # un ciclo
python bridge.py --simulate --enable-balancer            # loop

# DRY-RUN — lee del bus real, pero NO publica MQTT ni escribe MOSFET.
python bridge.py --dry-run

# LIVE — serial + MQTT reales. MOSFET solo si balancer.enabled Y allow_mosfet_writes.
python bridge.py
```

En `--simulate` el banco es sintético (config `sim:`), con un pack desbalanceado
para ver al balanceador decidir aislarlo (charge-only). En `--simulate` y
`--dry-run` NUNCA se escribe a un BMS ni se publica nada.

## Roadmap

1. ✅ Logger lectura (`logger.py`)
2. ✅ Feed Ekrano vía MQTT (`bridge.py` + `jkpb/mqtt_publisher.py`) — agregado + CCL/DCL
3. ✅ Balanceador per-pack (`jkpb/balancer.py` + `jkpb/control.py`) — lógica + gates.
   Control MOSFET: `BatChargeEN` 0x0070 / `BatDisChargeEN` 0x0074, FC16.
   **Write frame SIN verificar en hardware** → `allow_mosfet_writes: false` hasta validarlo.

## ⚠️ Seguridad (fases 2-3, NO en este logger)

- Aislar un pack (discharge-FET off) en banco 16P bajo carga → los otros 15
  asumen toda la corriente. Conmutar MOSFET bajo corriente alta puede dañarlo.
- La RPi como único enlace de batería al Ekrano = punto único de fallo. Hay que
  configurar DVCC para parar inversor si pierde comms (watchdog).
- El balanceador necesita: límite de packs aislados a la vez, comprobar corriente
  baja antes de conmutar, y fallback automático a estado normal.
