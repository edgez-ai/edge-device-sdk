#!/usr/bin/env python3
"""
RS485 Modbus RTU vibration sensor example via LwM2M REST API.

This example targets the vibration sensor described in vibration-sensor.txt:
- Protocol: Modbus RTU
- Serial format: 9600 8N1
- Default slave address: 0x50

It demonstrates:
1) One-shot reads of predefined register groups
2) Polling sensor values continuously
3) Raw register reads for custom debugging

Examples:
    # Read common groups once (accel/velocity/temp/displacement/frequency)
    python vibration_modbus_rtu_rest.py \
        --base-url http://192.168.10.177:8088 \
        --client B43A45A45A08

    # Poll basic groups every second, 10 samples
    python vibration_modbus_rtu_rest.py \
        --base-url http://192.168.10.177:8088 \
        --client B43A45A45A08 \
        --action poll --group basic --interval 1 --samples 10

    # Read X-axis advanced feature registers (0x47..0x52)
    python vibration_modbus_rtu_rest.py \
        --base-url http://192.168.10.177:8088 \
        --client B43A45A45A08 \
        --group x-advanced

    # Raw read (address/count can be hex)
    python vibration_modbus_rtu_rest.py \
        --base-url http://192.168.10.177:8088 \
        --client B43A45A45A08 \
        --action raw --address 0x34 --count 3
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import Lwm2mRestClient, RestConfig
from core.uart_client import RS485_RESOURCES, UartSession
from driver import FlowMeter


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_signed16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def _parse_int(text: str) -> int:
    return int(text, 0)


class ModbusRtuReader:
    def __init__(
        self,
        client: Lwm2mRestClient,
        endpoint: str,
        *,
        instance: int,
        baudrate: int,
        tx_pin: Optional[int],
        rx_pin: Optional[int],
        unit_id: int,
        rs485_mode: int,
        rx_size: int,
        timeout_s: float,
        debug: bool,
    ):
        self.session = UartSession(
            client=client,
            endpoint=endpoint,
            instance=instance,
            resources=RS485_RESOURCES,
            debug=debug,
        )
        self.baudrate = baudrate
        self.tx_pin = tx_pin
        self.rx_pin = rx_pin
        self.unit_id = unit_id
        self.rs485_mode = rs485_mode
        self.rx_size = rx_size
        self.timeout_s = timeout_s
        self.debug = debug

    def _log(self, message: str) -> None:
        if self.debug:
            print(f"[modbus] {message}", file=sys.stderr, flush=True)

    def connect(self) -> None:
        self._log(
            f"open rs485 baud={self.baudrate} unit=0x{self.unit_id:02X} tx={self.tx_pin} rx={self.rx_pin}"
        )
        self.session.open(
            baudrate=self.baudrate,
            tx_pin=self.tx_pin,
            rx_pin=self.rx_pin,
            rx_size=self.rx_size,
            modbus_unit_id=self.unit_id,
            mode=self.rs485_mode,
        )

    def disconnect(self) -> None:
        try:
            self.session.close()
        except Exception:
            pass

    def read_holding_registers(self, address: int, count: int) -> Optional[list[int]]:
        request = FlowMeter.build_read_holding_request(self.unit_id, address, count)
        self._log(f"tx: {request.hex()}")

        self.session.reset_cursor()
        self.session.write(request)

        expected_data_bytes = count * 2
        buffer = b""
        deadline = time.time() + self.timeout_s

        while time.time() < deadline:
            chunk = self.session.read()
            if chunk:
                buffer += chunk
                frame = FlowMeter.extract_frame(buffer, self.unit_id, 0x03, expected_data_bytes)
                if frame:
                    payload = frame[3:-2]
                    regs: list[int] = []
                    for i in range(0, len(payload), 2):
                        regs.append((payload[i] << 8) | payload[i + 1])
                    self._log(f"rx regs: {[hex(v) for v in regs]}")
                    return regs
            time.sleep(0.02)
        return None


def decode_accel(regs: list[int]) -> dict:
    # Datasheet: AX/AY/AZ = signed16 / 32768 * 16 g
    keys = ["ax_g", "ay_g", "az_g"]
    out: dict = {}
    for key, raw in zip(keys, regs):
        out[key] = _to_signed16(raw) / 32768.0 * 16.0
    return out


def decode_velocity(regs: list[int]) -> dict:
    # Datasheet: VX/VY/VZ (mm/s) = raw / 100
    keys = ["vx_mm_s", "vy_mm_s", "vz_mm_s"]
    return {key: raw / 100.0 for key, raw in zip(keys, regs)}


def decode_temp(regs: list[int]) -> dict:
    # Datasheet: TEMP (degC) = signed16 / 100
    return {"temp_c": _to_signed16(regs[0]) / 100.0}


def decode_displacement(regs: list[int]) -> dict:
    # Datasheet table uses um; mm is convenient for output.
    keys_um = ["dx_um", "dy_um", "dz_um"]
    keys_mm = ["dx_mm", "dy_mm", "dz_mm"]
    out: dict = {}
    for key_um, key_mm, raw in zip(keys_um, keys_mm, regs):
        out[key_um] = raw
        out[key_mm] = raw / 1000.0
    return out


def decode_frequency(regs: list[int]) -> dict:
    # Datasheet: HZX/HZY/HZZ (Hz) = raw / 10
    keys = ["hzx_hz", "hzy_hz", "hzz_hz"]
    return {key: raw / 10.0 for key, raw in zip(keys, regs)}


def decode_scaled_1000(regs: list[int], names: list[str]) -> dict:
    return {name: raw / 1000.0 for name, raw in zip(names, regs)}


GROUPS: Dict[str, dict] = {
    "accel": {
        "address": 0x34,
        "count": 3,
        "decoder": decode_accel,
    },
    "velocity": {
        "address": 0x3A,
        "count": 3,
        "decoder": decode_velocity,
    },
    "temp": {
        "address": 0x40,
        "count": 1,
        "decoder": decode_temp,
    },
    "displacement": {
        "address": 0x41,
        "count": 3,
        "decoder": decode_displacement,
    },
    "frequency": {
        "address": 0x44,
        "count": 3,
        "decoder": decode_frequency,
    },
    "x-advanced": {
        "address": 0x47,
        "count": 12,
        "decoder": lambda regs: decode_scaled_1000(
            regs,
            [
                "cfx",
                "kx",
                "aavgx",
                "varx",
                "rrax",
                "wix",
                "pix",
                "pcx",
                "skx",
                "vrmsx_mm_s",
                "vkx",
                "drmsx_mm",
            ],
        ),
    },
    "y-advanced": {
        "address": 0x53,
        "count": 12,
        "decoder": lambda regs: decode_scaled_1000(
            regs,
            [
                "cfy",
                "ky",
                "aavgy",
                "vary",
                "rray",
                "wiy",
                "piy",
                "pcy",
                "sky",
                "vrmsy_mm_s",
                "vky",
                "drmsy_mm",
            ],
        ),
    },
    "z-advanced": {
        "address": 0x5F,
        "count": 12,
        "decoder": lambda regs: decode_scaled_1000(
            regs,
            [
                "cfz",
                "kz",
                "aavgz",
                "varz",
                "rraz",
                "wiz",
                "piz",
                "pcz",
                "skz",
                "vrmsz_mm_s",
                "vkz",
                "drmsz_mm",
            ],
        ),
    },
}

BASIC_GROUP_ORDER = ["accel", "velocity", "temp", "displacement", "frequency"]
ALL_GROUP_ORDER = BASIC_GROUP_ORDER + ["x-advanced", "y-advanced", "z-advanced"]
FULL_BLOCK_START = 0x34
FULL_BLOCK_END = 0x9A
FULL_BLOCK_COUNT = FULL_BLOCK_END - FULL_BLOCK_START + 1


def emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=True), flush=True)
        return
    print(payload, flush=True)


def read_group(reader: ModbusRtuReader, group_name: str) -> dict:
    group = GROUPS[group_name]
    address = group["address"]
    count = group["count"]
    decoder: Callable[[list[int]], dict] = group["decoder"]
    regs = reader.read_holding_registers(address, count)
    if regs is None:
        return {
            "ok": False,
            "group": group_name,
            "address": address,
            "count": count,
            "error": "modbus timeout",
        }
    return {
        "ok": True,
        "group": group_name,
        "address": address,
        "count": count,
        "registers": regs,
        "values": decoder(regs),
    }


def _slice_registers(block: list[int], start_addr: int, count: int) -> list[int]:
    offset = start_addr - FULL_BLOCK_START
    return block[offset : offset + count]


def run_full_block_action(reader: ModbusRtuReader) -> dict:
    regs = reader.read_holding_registers(FULL_BLOCK_START, FULL_BLOCK_COUNT)
    if regs is None:
        return {
            "ok": False,
            "results": [
                {
                    "ok": False,
                    "group": "full-block",
                    "address": FULL_BLOCK_START,
                    "count": FULL_BLOCK_COUNT,
                    "error": "modbus timeout",
                }
            ],
        }

    results = []
    for name in ALL_GROUP_ORDER:
        group = GROUPS[name]
        addr = group["address"]
        count = group["count"]
        decoder: Callable[[list[int]], dict] = group["decoder"]
        part = _slice_registers(regs, addr, count)
        results.append(
            {
                "ok": True,
                "group": name,
                "address": addr,
                "count": count,
                "registers": part,
                "values": decoder(part),
            }
        )

    return {
        "ok": True,
        "read_mode": "single-request",
        "block_address": FULL_BLOCK_START,
        "block_count": FULL_BLOCK_COUNT,
        "results": results,
    }


def run_read_action(reader: ModbusRtuReader, group: str) -> dict:
    if group == "full-block":
        return run_full_block_action(reader)

    selected = []
    if group == "basic":
        selected = BASIC_GROUP_ORDER
    elif group == "all":
        selected = ALL_GROUP_ORDER
    else:
        selected = [group]

    results = [read_group(reader, name) for name in selected]
    return {
        "ok": all(item["ok"] for item in results),
        "results": results,
    }


def run_raw_action(reader: ModbusRtuReader, address: int, count: int) -> dict:
    regs = reader.read_holding_registers(address, count)
    if regs is None:
        return {
            "ok": False,
            "address": address,
            "count": count,
            "error": "modbus timeout",
        }
    return {
        "ok": True,
        "address": address,
        "count": count,
        "registers": regs,
        "registers_hex": [hex(v) for v in regs],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="RS485 Modbus RTU vibration sensor example")
    parser.add_argument("--base-url", default="http://192.168.10.177:8088", help="REST API base URL")
    parser.add_argument("--client", required=True, help="LwM2M endpoint, e.g. B43A45A45A08")
    parser.add_argument("--instance", type=int, default=0, help="RS485 object instance")
    parser.add_argument("--tx-pin", type=int, default=None, help="RS485 TX pin (optional)")
    parser.add_argument("--rx-pin", type=int, default=None, help="RS485 RX pin (optional)")
    parser.add_argument("--baud", type=int, default=9600, help="RS485 baudrate")
    parser.add_argument("--unit-id", type=_parse_int, default=0x50, help="Modbus slave id (default: 0x50)")
    parser.add_argument("--rx-size", type=int, default=256, help="RS485 RX buffer size")
    parser.add_argument("--rs485-mode", type=int, default=0, help="RS485 mode value")
    parser.add_argument("--modbus-timeout", type=float, default=1.0, help="Modbus response timeout (seconds)")
    parser.add_argument("--json", action="store_true", help="Output JSON lines")
    parser.add_argument("--quiet", action="store_true", help="Suppress debug logs")

    parser.add_argument(
        "--action",
        choices=["read", "poll", "raw"],
        default="read",
        help="read: one-shot group read; poll: repeated group read; raw: raw registers",
    )
    parser.add_argument(
        "--group",
        choices=["accel", "velocity", "temp", "displacement", "frequency", "x-advanced", "y-advanced", "z-advanced", "basic", "all", "full-block"],
        default="basic",
        help="Register group for read/poll",
    )
    parser.add_argument("--interval", type=float, default=1.0, help="Polling interval seconds")
    parser.add_argument("--samples", type=int, default=1, help="Number of poll samples; 0 means unlimited")
    parser.add_argument("--address", type=_parse_int, default=0x34, help="Raw read start register")
    parser.add_argument("--count", type=int, default=3, help="Raw read register count")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    config = RestConfig(base_url=args.base_url)
    client = Lwm2mRestClient(config)

    reader = ModbusRtuReader(
        client=client,
        endpoint=args.client,
        instance=args.instance,
        baudrate=args.baud,
        tx_pin=args.tx_pin,
        rx_pin=args.rx_pin,
        unit_id=args.unit_id,
        rs485_mode=args.rs485_mode,
        rx_size=args.rx_size,
        timeout_s=args.modbus_timeout,
        debug=not args.quiet,
    )

    try:
        reader.connect()

        if args.action == "raw":
            payload = run_raw_action(reader, args.address, args.count)
            payload.update({"time": _ts(), "endpoint": args.client, "action": "raw"})
            emit(payload, as_json=args.json)
            return 0 if payload["ok"] else 1

        if args.action == "read":
            payload = run_read_action(reader, args.group)
            payload.update({"time": _ts(), "endpoint": args.client, "action": "read", "group": args.group})
            emit(payload, as_json=args.json)
            return 0 if payload["ok"] else 1

        interval = max(0.0, args.interval)
        sample_count = max(0, args.samples)
        n = 0
        while True:
            payload = run_read_action(reader, args.group)
            payload.update({"time": _ts(), "endpoint": args.client, "action": "poll", "group": args.group, "sample": n + 1})
            emit(payload, as_json=args.json)

            n += 1
            if sample_count > 0 and n >= sample_count:
                break
            if interval <= 0:
                break
            time.sleep(interval)

        return 0
    except KeyboardInterrupt:
        print("Interrupted", file=sys.stderr)
        return 130
    except Exception as exc:
        err = {"time": _ts(), "endpoint": args.client, "ok": False, "error": str(exc)}
        emit(err, as_json=args.json)
        return 1
    finally:
        reader.disconnect()


if __name__ == "__main__":
    raise SystemExit(main())
