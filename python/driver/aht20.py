from __future__ import annotations

import time
from typing import Dict, Optional

from core.i2c_client import I2CSession

AHT20_CMD_INIT = [0xBE, 0x08, 0x00]
AHT20_CMD_TRIGGER = [0xAC, 0x33, 0x00]


def _crc8(data: bytes) -> int:
    crc = 0xFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x80:
                crc = ((crc << 1) ^ 0x31) & 0xFF
            else:
                crc = (crc << 1) & 0xFF
    return crc


def _parse_measurement(data: bytes) -> Dict[str, Optional[float]]:
    if len(data) < 6:
        return {
            "temperature_c": None,
            "humidity_rh": None,
            "raw_data_hex": data.hex(),
            "error": "Incomplete data received",
        }

    status = data[0]
    humidity_raw = (data[1] << 12) | (data[2] << 4) | (data[3] >> 4)
    temperature_raw = ((data[3] & 0x0F) << 16) | (data[4] << 8) | data[5]

    humidity = humidity_raw * 100.0 / 1048576.0
    temperature = temperature_raw * 200.0 / 1048576.0 - 50.0

    payload = {
        "temperature_c": temperature,
        "humidity_rh": humidity,
        "status": status,
        "busy": bool(status & 0x80),
        "calibrated": bool(status & 0x08),
        "raw_data_hex": data.hex(),
    }

    if len(data) >= 7:
        payload["crc_valid"] = _crc8(data[:6]) == data[6]

    return payload


def read_aht20(session: I2CSession, delay_s: float = 0.08) -> Dict[str, Optional[float]]:
    session.reset_cursor()
    session.write(AHT20_CMD_INIT)
    time.sleep(0.01)

    session.reset_cursor()
    session.write(AHT20_CMD_TRIGGER)
    time.sleep(delay_s)

    session.reset_cursor()
    session.set_rx_size(7)
    data = session.read()

    return _parse_measurement(data)
