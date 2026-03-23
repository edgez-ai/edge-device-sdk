from __future__ import annotations

import base64
import json
import sys
import time
from typing import Any, Dict, Optional, Sequence

import requests

from .i2c_client import Lwm2mRestClient

# LwM2M object and resource IDs for the custom RS485 and UART interface objects
RS485_OBJECT_ID = 10252
UART_OBJECT_ID = 10253

# Keep IDs in sync with main/lwm2m/object_interface.h
RS485_RESOURCES: Dict[str, int] = {
    "type": 0,
    "enabled": 1,
    "open_state": 2,
    "tx_bytes": 3,
    "rx_bytes": 4,
    "error_count": 5,
    "last_error": 6,
    "baudrate": 7,
    "modbus_unit_id": 8,
    "mode": 9,
    "reset_counters": 10,
    "stats_window_ms": 11,
    "tx_rate": 12,
    "rx_rate": 13,
    "tx_payload": 14,
    "rx_buffer_pos": 15,
    "rx_chunk": 16,
    "rx_buffer_size": 17,
    "tx_pin": 18,
    "rx_pin": 19,
}

# Keep IDs in sync with main/lwm2m/object_interface.h (UART resources)
UART_RESOURCES: Dict[str, int] = {
    "type": 0,
    "enabled": 1,
    "open_state": 2,
    "tx_bytes": 3,
    "rx_bytes": 4,
    "error_count": 5,
    "last_error": 6,
    "baudrate": 7,
    "mode": 8,
    "reset_counters": 9,
    "stats_window_ms": 10,
    "tx_rate": 11,
    "rx_rate": 12,
    "tx_payload": 13,
    "rx_buffer_pos": 14,
    "rx_chunk": 15,
    "rx_buffer_size": 16,
    "tx_pin": 17,
    "rx_pin": 18,
}


def _extract_bytes_payload(client: Lwm2mRestClient, data: Any) -> bytes:
    def _extract_senml_bytes(payload: Any) -> Optional[bytes]:
        if isinstance(payload, list) and payload:
            return _extract_senml_bytes(payload[0])
        if not isinstance(payload, dict):
            return None
        # Check for "vd" key explicitly (empty string is valid!)
        if "vd" in payload:
            val = payload["vd"]
        elif "data" in payload:
            val = payload["data"]
        else:
            val = None
        if val is None:
            return b""  # Key exists but value is None/missing -> empty
        if isinstance(val, str):
            if val == "":
                return b""  # Empty string -> empty bytes
            try:
                return base64.b64decode(val)
            except Exception:
                return None
        if isinstance(val, (bytes, bytearray)):
            return bytes(val)
        return None

    if isinstance(data, bytes):
        if data.startswith((b"{", b"[")):
            try:
                parsed = json.loads(data.decode("utf-8"))
                senml_bytes = _extract_senml_bytes(parsed)
                if senml_bytes is not None:
                    return senml_bytes
                val = client._decode_value(parsed)
                if isinstance(val, (bytes, bytearray)):
                    return bytes(val)
                if isinstance(val, str):
                    try:
                        return base64.b64decode(val)
                    except Exception:
                        return val.encode()
            except Exception:
                return b""
        return data
    if isinstance(data, str):
        if data.startswith(("{", "[")):
            try:
                parsed = json.loads(data)
                senml_bytes = _extract_senml_bytes(parsed)
                if senml_bytes is not None:
                    return senml_bytes
            except Exception:
                pass
        try:
            return base64.b64decode(data)
        except Exception:
            return data.encode()
    if isinstance(data, dict):
        senml_bytes = _extract_senml_bytes(data)
        if senml_bytes is not None:
            return senml_bytes
        val = data.get("value") if "value" in data else data.get("content") or data.get("bv")
        if isinstance(val, str):
            try:
                return base64.b64decode(val)
            except Exception:
                return val.encode()
        if isinstance(val, (bytes, bytearray)):
            return bytes(val)
    return b""


class UartSession:
    def __init__(
        self,
        client: Lwm2mRestClient,
        endpoint: str,
        instance: int = 0,
        *,
        object_id: int = RS485_OBJECT_ID,
        resources: Optional[Dict[str, int]] = None,
        debug: bool = True,
    ):
        self.client = client
        self.endpoint = endpoint
        self.instance = instance
        self.object_id = object_id
        self.resources = resources or RS485_RESOURCES
        self.debug = debug

    def _log(self, msg: str) -> None:
        if self.debug:
            print(f"[UartSession] {msg}", file=sys.stderr, flush=True)

    def _has(self, name: str) -> bool:
        return name in self.resources

    def _write_res(self, name: str, value: Any, is_bytes: bool = False) -> None:
        """Write a resource with logging and small delay for sequencing."""
        res_id = self.resources.get(name)
        if res_id is None:
            self._log(f"SKIP {name}: not in resources")
            return
        self._log(f"WRITE {name}(res={res_id}) = {value}")
        self.client.write_resource(
            self.endpoint, self.object_id, self.instance, res_id, value, is_bytes=is_bytes
        )
        time.sleep(0.05)  # Small delay to ensure write completes

    def _is_open(self) -> bool:
        res_id = self.resources.get("open_state")
        if res_id is None:
            return False
        try:
            value = self.client.read_resource(self.endpoint, self.object_id, self.instance, res_id)
        except Exception as exc:
            self._log(f"read open_state failed: {exc}")
            return False

        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "open", "on"}
        return False

    def is_enabled(self) -> bool:
        res_id = self.resources.get("enabled")
        if res_id is None:
            return True
        try:
            value = self.client.read_resource(self.endpoint, self.object_id, self.instance, res_id)
        except Exception as exc:
            self._log(f"read enabled failed: {exc}")
            return False

        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "enabled", "on"}
        return False

    def set_enabled(self, enabled: bool) -> None:
        if not self._has("enabled"):
            self._log("SKIP enabled: not in resources")
            return
        self._write_res("enabled", "true" if enabled else "false")

    def open(
        self,
        *,
        baudrate: int = 115200,
        tx_pin: Optional[int] = None,
        rx_pin: Optional[int] = None,
        rx_size: int = 1024,
        modbus_unit_id: int = 1,
        mode: Optional[int] = None,
    ) -> None:
        self._log(f"open(baud={baudrate}, tx={tx_pin}, rx={rx_pin}, rx_size={rx_size})")

        self.set_enabled(True)

        # Always close first to guarantee a clean reconnect sequence.
        if self._has("open_state"):
            self._log("forcing close before open to allow reconnect/reconfiguration")
            self._write_res("open_state", "false")
            time.sleep(0.1)
        
        # Configure pins first
        if tx_pin is not None and self._has("tx_pin"):
            self._write_res("tx_pin", tx_pin)
        if rx_pin is not None and self._has("rx_pin"):
            self._write_res("rx_pin", rx_pin)
        
        # Configure baudrate
        if baudrate is not None:
            if self._has("baudrate"):
                self._write_res("baudrate", baudrate)
            elif self._has("i2c_address"):
                self._write_res("i2c_address", baudrate)

        if self._has("baudrate"):
            try:
                effective_baud = self.client.read_resource(
                    self.endpoint,
                    self.object_id,
                    self.instance,
                    self.resources["baudrate"],
                )
                self._log(f"effective baud resource now={effective_baud}")
            except Exception as exc:
                self._log(f"readback baudrate failed: {exc}")
        
        # Configure buffer size
        if rx_size is not None and self._has("rx_buffer_size"):
            self._write_res("rx_buffer_size", rx_size)
        
        # Configure modbus unit id if present
        if modbus_unit_id is not None and self._has("modbus_unit_id"):
            self._write_res("modbus_unit_id", modbus_unit_id)
        
        # Configure mode if present
        if mode is not None and self._has("mode"):
            self._write_res("mode", mode)
        
        # Now open with all config in place
        if self._has("open_state"):
            self._log("opening UART with configured parameters")
            self._write_res("open_state", "true")
        
        self.reset_cursor()
        self._log("open complete")

    def close(self) -> None:
        self._log("close()")
        if self._has("open_state"):
            self._write_res("open_state", "false")

    def disable(self) -> None:
        self._log("disable()")
        self.close()
        self.set_enabled(False)

    def reset_cursor(self) -> None:
        if self._has("rx_buffer_pos"):
            self._write_res("rx_buffer_pos", 0)

    def set_rx_size(self, size: int) -> None:
        if self._has("rx_buffer_size"):
            self._write_res("rx_buffer_size", size)

    def write(self, payload: Sequence[int]) -> None:
        if self.debug:
            self._log(f"WRITE tx_payload: {bytes(payload).hex()}")
        self.client.write_resource(
            self.endpoint,
            self.object_id,
            self.instance,
            self.resources["tx_payload"],
            bytes(payload),
            is_bytes=True,
        )

    def read(self) -> bytes:
        try:
            data = self.client.read_resource(
                self.endpoint,
                self.object_id,
                self.instance,
                self.resources["rx_chunk"],
                headers={"Accept": "application/octet-stream"},
            )
        except requests.RequestException:
            return b""
        result = _extract_bytes_payload(self.client, data)
        if not result:
            try:
                fallback = self.client.read_resource(
                    self.endpoint,
                    self.object_id,
                    self.instance,
                    self.resources["rx_chunk"],
                )
                result = _extract_bytes_payload(self.client, fallback)
            except requests.RequestException:
                return b""
        if self.debug and result:
            self._log(f"READ rx_chunk: {result.hex()}")
        return result
