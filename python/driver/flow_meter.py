from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional, Sequence, Tuple

from core.uart_client import UartSession


@dataclass
class FlowMeterConfig:
    unit_id: int = 1
    register_address: int = 0
    register_count: int = 4
    flow_scale: float = 100000.0
    volume_scale: float = 10000.0
    timeout_s: float = 1.0


class FlowMeter:
    def __init__(self, session: UartSession, config: Optional[FlowMeterConfig] = None):
        self.session = session
        self.config = config or FlowMeterConfig()

    @staticmethod
    def decode_bcd_32(value: int) -> int:
        return (
            ((value >> 28) & 0xF) * 10000000
            + ((value >> 24) & 0xF) * 1000000
            + ((value >> 20) & 0xF) * 100000
            + ((value >> 16) & 0xF) * 10000
            + ((value >> 12) & 0xF) * 1000
            + ((value >> 8) & 0xF) * 100
            + ((value >> 4) & 0xF) * 10
            + (value & 0xF)
        )

    @staticmethod
    def crc16_modbus(data: bytes) -> int:
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc = (crc >> 1) ^ 0xA001
                else:
                    crc >>= 1
        return crc & 0xFFFF

    @classmethod
    def build_read_holding_request(cls, unit_id: int, address: int, count: int) -> bytes:
        frame = bytes([unit_id, 0x03, (address >> 8) & 0xFF, address & 0xFF, (count >> 8) & 0xFF, count & 0xFF])
        crc = cls.crc16_modbus(frame)
        return frame + bytes([crc & 0xFF, (crc >> 8) & 0xFF])

    @classmethod
    def extract_frame(cls, buffer: bytes, unit_id: int, function: int, byte_count: int) -> Optional[bytes]:
        expected_len = 5 + byte_count
        if len(buffer) < expected_len:
            return None
        for start in range(0, len(buffer) - expected_len + 1):
            if buffer[start] != unit_id or buffer[start + 1] != function or buffer[start + 2] != byte_count:
                continue
            frame = buffer[start : start + expected_len]
            crc = cls.crc16_modbus(frame[:-2])
            if frame[-2] == (crc & 0xFF) and frame[-1] == ((crc >> 8) & 0xFF):
                return frame
        return None

    def read_holding_registers(self, address: int, count: int) -> Optional[Sequence[int]]:
        request = self.build_read_holding_request(self.config.unit_id, address, count)
        self.session.reset_cursor()
        self.session.write(request)

        byte_count = count * 2
        buffer = b""
        deadline = time.time() + self.config.timeout_s
        while time.time() < deadline:
            chunk = self.session.read()
            if chunk:
                buffer += chunk
                frame = self.extract_frame(buffer, self.config.unit_id, 0x03, byte_count)
                if frame:
                    payload = frame[3:-2]
                    registers = []
                    for i in range(0, len(payload), 2):
                        registers.append((payload[i] << 8) | payload[i + 1])
                    return registers
            time.sleep(0.02)
        return None

    def read_flow_and_total_volume(self) -> Optional[Tuple[float, float]]:
        regs = self.read_holding_registers(self.config.register_address, self.config.register_count)
        if not regs or len(regs) < 4:
            return None
        flow_rate_raw = (regs[2] << 16) | regs[3]
        total_volume_raw = (regs[0] << 16) | regs[1]
        flow_rate = self.decode_bcd_32(flow_rate_raw) / self.config.flow_scale
        total_volume = self.decode_bcd_32(total_volume_raw) / self.config.volume_scale
        return flow_rate, total_volume
