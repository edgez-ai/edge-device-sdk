from __future__ import annotations

import time
from typing import Dict, Optional

from core.i2c_client import I2CSession

ENS210_REG_SENS_START = 0x22
ENS210_REG_T_VAL = 0x30
ENS210_REG_H_VAL = 0x33


def read_ens210(session: I2CSession, delay_s: float = 0.15) -> Dict[str, float]:
    session.set_rx_size(3)
    session.reset_cursor()
    session.write([ENS210_REG_SENS_START, 0x03])
    time.sleep(delay_s)

    session.reset_cursor()
    session.set_rx_size(3)
    session.write([ENS210_REG_T_VAL])
    time.sleep(0.005)
    temp_raw = session.read()

    session.reset_cursor()
    session.set_rx_size(3)
    session.write([ENS210_REG_H_VAL])
    time.sleep(0.005)
    hum_raw = session.read()

    def parse_triplet(raw: bytes) -> Optional[int]:
        if len(raw) < 3:
            return None
        valid = raw[2] & 0x01
        if not valid:
            return None
        return (raw[1] << 8) | raw[0]

    t_val = parse_triplet(temp_raw)
    h_val = parse_triplet(hum_raw)

    temperature = None
    humidity = None
    if t_val is not None:
        temperature = (t_val / 64.0) - 273.15
    if h_val is not None:
        humidity = min(max(h_val / 512.0, 0.0), 100.0)

    return {
        "temperature_c": temperature,
        "humidity_rh": humidity,
        "temp_raw": temp_raw.hex(),
        "hum_raw": hum_raw.hex(),
    }
