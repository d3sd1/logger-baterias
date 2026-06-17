"""Publish the bank aggregate to Venus OS via dbus-mqtt-battery.

Topic:   N/<VRM_ID>/battery/<INSTANCE>/JsonData
Payload: JSON schema expected by mr-manuel/venus-os_dbus-mqtt-battery.

The Ekrano GX runs the dbus-mqtt-battery service + an MQTT broker; this
publishes to it over LAN. CCL/DCL (Info.MaxCharge/DischargeCurrent) drive DVCC.
"""

import json
from typing import Optional

import paho.mqtt.client as mqtt

from .aggregate import BankAggregate


class VenusBatteryPublisher:
    def __init__(self, host: str, vrm_id: str, instance: int = 1,
                 port: int = 1883, username: str = "", password: str = ""):
        self.host = host
        self.port = port
        self.topic = f"N/{vrm_id}/battery/{instance}/JsonData"
        self._c = mqtt.Client()
        if username:
            self._c.username_pw_set(username, password)
        self._connected = False

    def connect(self):
        self._c.connect(self.host, self.port, keepalive=30)
        self._c.loop_start()
        self._connected = True

    def disconnect(self):
        if self._connected:
            self._c.loop_stop()
            self._c.disconnect()
            self._connected = False

    def publish(self, agg: BankAggregate, limits: dict) -> None:
        """`limits` = {max_charge_voltage, max_charge_current, max_discharge_current}."""
        payload = {
            "Dc": {
                "Power": agg.power,
                "Voltage": agg.voltage,
                "Current": agg.current,
                "Temperature": agg.temp_max_c,
            },
            "Soc": agg.soc,
            "Capacity": agg.cap_remain_ah,
            "Balancing": int(agg.balancing),
            "System": {
                "MinCellVoltage": agg.cell_min_mv / 1000.0,
                "MaxCellVoltage": agg.cell_max_mv / 1000.0,
                "NrOfModulesOnline": agg.pack_count,
            },
            "Info": {
                "MaxChargeVoltage": limits["max_charge_voltage"],
                "MaxChargeCurrent": limits["max_charge_current"],
                "MaxDischargeCurrent": limits["max_discharge_current"],
            },
            "Io": {
                "AllowToCharge": int(agg.allow_charge),
                "AllowToDischarge": int(agg.allow_discharge),
            },
        }
        self._c.publish(self.topic, json.dumps(payload), qos=0, retain=False)
