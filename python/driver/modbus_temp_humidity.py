from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Sequence

from core.uart_client import UartSession

from .flow_meter import FlowMeter


@dataclass
class ModbusTempHumidityConfig:
    unit_id: int = 1
    register_address: int = 0
    register_count: int = 2
    function_code: int = 0x03
    temperature_scale: float = 10.0
    humidity_scale: float = 10.0
    temperature_index: int = 0
    humidity_index: Optional[int] = 1
    timeout_s: float = 1.0


class ModbusTempHumiditySensor:
    def __init__(self, session: UartSession, config: Optional[ModbusTempHumidityConfig] = None):
        self.session = session
        self.config = config or ModbusTempHumidityConfig()

    @staticmethod
    def _decode_signed_16(value: int) -> int:
        return value - 0x10000 if value & 0x8000 else value

    @staticmethod
    def _build_read_request(unit_id: int, function_code: int, address: int, count: int) -> bytes:
        frame = bytes([unit_id, function_code, (address >> 8) & 0xFF, address & 0xFF, (count >> 8) & 0xFF, count & 0xFF])
        crc = FlowMeter.crc16_modbus(frame)
        return frame + bytes([crc & 0xFF, (crc >> 8) & 0xFF])

    def read_registers(self, address: int, count: int) -> Optional[Sequence[int]]:
        request = self._build_read_request(self.config.unit_id, self.config.function_code, address, count)
        self.session.reset_cursor()
        self.session.write(request)

        byte_count = count * 2
        buffer = b""
        deadline = time.time() + self.config.timeout_s
        while time.time() < deadline:
            chunk = self.session.read()
            if chunk:
                buffer += chunk
                frame = FlowMeter.extract_frame(buffer, self.config.unit_id, self.config.function_code, byte_count)
                if frame:
                    payload = frame[3:-2]
                    registers = []
                    for i in range(0, len(payload), 2):
                        registers.append((payload[i] << 8) | payload[i + 1])
                    return registers
            time.sleep(0.02)
        return None

    def read_temperature_humidity(self) -> Optional[dict]:
        regs = self.read_registers(self.config.register_address, self.config.register_count)
        if not regs:
            return None

        payload: dict = {
            "registers": [int(v) & 0xFFFF for v in regs],
            "unit_id": self.config.unit_id,
            "function_code": self.config.function_code,
            "address": self.config.register_address,
        }

        t_index = self.config.temperature_index
        if 0 <= t_index < len(regs):
            temp_raw = int(regs[t_index]) & 0xFFFF
            # The protocol marks 0x8000 as sensor probe error.
            if temp_raw == 0x8000:
                payload["temperature_c"] = None
                payload["temperature_probe_error"] = True
            else:
                temp_signed = self._decode_signed_16(temp_raw)
                payload["temperature_c"] = temp_signed / self.config.temperature_scale
                payload["temperature_probe_error"] = False

        h_index = self.config.humidity_index
        if h_index is not None and 0 <= h_index < len(regs):
            humidity_raw = int(regs[h_index]) & 0xFFFF
            payload["humidity_rh"] = humidity_raw / self.config.humidity_scale

        return payload