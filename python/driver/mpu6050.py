from __future__ import annotations

import time
from typing import Dict

from core.i2c_client import I2CSession

MPU6050_REG_PWR_MGMT_1 = 0x6B
MPU6050_REG_ACCEL_CONFIG = 0x1C
MPU6050_REG_GYRO_CONFIG = 0x1B
MPU6050_REG_ACCEL_XOUT_H = 0x3B


def _to_int16(msb: int, lsb: int) -> int:
    value = (msb << 8) | lsb
    if value & 0x8000:
        return value - 0x10000
    return value


def read_mpu6050(session: I2CSession, delay_s: float = 0.02) -> Dict[str, float]:
    session.reset_cursor()
    session.write([MPU6050_REG_PWR_MGMT_1, 0x00])
    time.sleep(0.01)

    session.reset_cursor()
    session.write([MPU6050_REG_ACCEL_CONFIG, 0x00])
    time.sleep(0.005)

    session.reset_cursor()
    session.write([MPU6050_REG_GYRO_CONFIG, 0x00])
    time.sleep(delay_s)

    session.reset_cursor()
    session.set_rx_size(14)
    session.write([MPU6050_REG_ACCEL_XOUT_H])
    time.sleep(0.005)
    data = session.read()

    if len(data) < 14:
        return {
            "accel_x_g": None,
            "accel_y_g": None,
            "accel_z_g": None,
            "gyro_x_dps": None,
            "gyro_y_dps": None,
            "gyro_z_dps": None,
            "raw_data_hex": data.hex(),
            "error": "Incomplete data received",
        }

    accel_x_raw = _to_int16(data[0], data[1])
    accel_y_raw = _to_int16(data[2], data[3])
    accel_z_raw = _to_int16(data[4], data[5])

    gyro_x_raw = _to_int16(data[8], data[9])
    gyro_y_raw = _to_int16(data[10], data[11])
    gyro_z_raw = _to_int16(data[12], data[13])

    accel_scale = 16384.0
    gyro_scale = 131.0

    return {
        "accel_x_g": accel_x_raw / accel_scale,
        "accel_y_g": accel_y_raw / accel_scale,
        "accel_z_g": accel_z_raw / accel_scale,
        "gyro_x_dps": gyro_x_raw / gyro_scale,
        "gyro_y_dps": gyro_y_raw / gyro_scale,
        "gyro_z_dps": gyro_z_raw / gyro_scale,
        "accel_x_raw": accel_x_raw,
        "accel_y_raw": accel_y_raw,
        "accel_z_raw": accel_z_raw,
        "gyro_x_raw": gyro_x_raw,
        "gyro_y_raw": gyro_y_raw,
        "gyro_z_raw": gyro_z_raw,
        "raw_data_hex": data.hex(),
    }