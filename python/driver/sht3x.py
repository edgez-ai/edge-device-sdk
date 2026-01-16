from __future__ import annotations

import time
from typing import Dict

from core.i2c_client import I2CSession

SHT3X_CMD_RESET = [0x30, 0xA2]
SHT3X_CMD_SINGLE_SHOT_HIGH = [0x2C, 0x06]
SHT3X_CMD_SINGLE_SHOT_MED = [0x2C, 0x0D]
SHT3X_CMD_SINGLE_SHOT_LOW = [0x2C, 0x10]


def read_sht3x(session: I2CSession, repeatability: str = "high", delay_s: float = 0.001) -> Dict[str, float]:
    session.reset_cursor()
    session.write(SHT3X_CMD_RESET)
    time.sleep(0.002)

    if repeatability == "high":
        cmd = SHT3X_CMD_SINGLE_SHOT_HIGH
    elif repeatability == "med":
        cmd = SHT3X_CMD_SINGLE_SHOT_MED
    else:
        cmd = SHT3X_CMD_SINGLE_SHOT_LOW

    session.set_rx_size(6)
    session.reset_cursor()

    session.write(cmd)
    time.sleep(delay_s)

    session.reset_cursor()
    session.set_rx_size(6)
    data = session.read()

    if len(data) < 6:
        return {
            "temperature_c": None,
            "humidity_rh": None,
            "raw_data_hex": data.hex(),
            "error": "Incomplete data received",
        }

    temp_raw = (data[0] << 8) | data[1]
    temp_crc = data[2]
    hum_raw = (data[3] << 8) | data[4]
    hum_crc = data[5]

    def crc8(chunk: bytes) -> int:
        crc = 0xFF
        for byte in chunk:
            crc ^= byte
            for _ in range(8):
                if crc & 0x80:
                    crc = (crc << 1) ^ 0x31
                else:
                    crc <<= 1
                crc &= 0xFF
        return crc

    temp_valid = crc8(data[0:2]) == temp_crc
    hum_valid = crc8(data[3:5]) == hum_crc

    temperature = None
    humidity = None

    if temp_valid:
        temperature = -45 + (175 * temp_raw / 65535.0)

    if hum_valid:
        humidity = 100 * hum_raw / 65535.0

    return {
        "temperature_c": temperature,
        "humidity_rh": humidity,
        "temp_raw": f"{data[0]:02x}{data[1]:02x}",
        "hum_raw": f"{data[3]:02x}{data[4]:02x}",
        "temp_crc_valid": temp_valid,
        "hum_crc_valid": hum_valid,
        "repeatability": repeatability,
    }
