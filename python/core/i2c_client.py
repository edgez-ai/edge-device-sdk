from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Sequence

import requests

# LwM2M object and resource IDs for the custom I2C interface object
I2C_OBJECT_ID = 10251

# Keep IDs in sync with main/lwm2m/object_interface.h
I2C_RESOURCES: Dict[str, int] = {
    "type": 0,
    "enabled": 1,
    "open_state": 2,
    "tx_bytes": 3,
    "rx_bytes": 4,
    "error_count": 5,
    "last_error": 6,
    "i2c_address": 7,
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


@dataclass
class RestConfig:
    base_url: str = "http://192.168.100.1:8088"
    timeout: float = 5.0


class Lwm2mRestClient:
    def __init__(self, config: RestConfig) -> None:
        self.config = config

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}{path}"

    def list_clients(self) -> Iterable[str]:
        url = self._url("/api/clients")
        resp = requests.get(url, timeout=self.config.timeout)
        resp.raise_for_status()
        data = resp.json()
        for item in data:
            endpoint = item.get("endpoint") or item.get("name") or item.get("id")
            if endpoint:
                yield endpoint

    def _decode_value(self, payload: Any) -> Any:
        if not isinstance(payload, dict):
            return payload
        if "content" in payload and "value" not in payload:
            val = payload["content"]
            if isinstance(val, str):
                try:
                    return base64.b64decode(val)
                except Exception:
                    return val
            return val
        if "value" in payload:
            val = payload["value"]
            if isinstance(val, str):
                try:
                    return base64.b64decode(val)
                except Exception:
                    return val
            return val
        if "values" in payload:
            return payload["values"]
        return payload

    def read_resource(self, endpoint: str, obj: int, inst: int, res: int, *, headers: Optional[Dict[str, str]] = None) -> Any:
        url = self._url(f"/api/clients/{endpoint}/{obj}/{inst}/{res}")
        resp = requests.get(url, headers=headers or {}, timeout=self.config.timeout)
        resp.raise_for_status()
        ct = resp.headers.get("Content-Type", "")
        if "octet-stream" in ct:
            return resp.content
        try:
            raw = resp.json()
        except Exception:
            return resp.content
        val = self._decode_value(raw)
        if val is None and resp.content:
            return resp.content
        return val

    def write_resource(self, endpoint: str, obj: int, inst: int, res: int, value: Any, is_bytes: bool = False) -> None:
        url = self._url(f"/api/clients/{endpoint}/{obj}/{inst}/{res}")
        headers = {"Content-Type": "application/octet-stream" if is_bytes else "text/plain"}
        data = value if is_bytes else str(value)
        resp = requests.put(url, data=data, headers=headers, timeout=self.config.timeout)
        resp.raise_for_status()

    def execute(self, endpoint: str, obj: int, inst: int, res: int) -> None:
        url = self._url(f"/api/clients/{endpoint}/{obj}/{inst}/{res}")
        resp = requests.post(url, timeout=self.config.timeout)
        resp.raise_for_status()

    def read_i2c(self, endpoint: str, instance: int, resource_names: Iterable[str]) -> Dict[str, Any]:
        readings: Dict[str, Any] = {}
        for name in resource_names:
            res_id = I2C_RESOURCES.get(name)
            if res_id is None:
                raise KeyError(f"Unknown I2C resource name: {name}")
            readings[name] = self.read_resource(endpoint, I2C_OBJECT_ID, instance, res_id)
        return readings

    def i2c_set_address(self, endpoint: str, instance: int, addr: int) -> None:
        self.write_resource(endpoint, I2C_OBJECT_ID, instance, I2C_RESOURCES["i2c_address"], addr)

    def i2c_set_enabled(self, endpoint: str, instance: int, enabled: bool) -> None:
        self.write_resource(endpoint, I2C_OBJECT_ID, instance, I2C_RESOURCES["enabled"], "true" if enabled else "false")

    def i2c_set_open(self, endpoint: str, instance: int, open_state: bool) -> None:
        self.write_resource(endpoint, I2C_OBJECT_ID, instance, I2C_RESOURCES["open_state"], "true" if open_state else "false")

    def i2c_reset_cursor(self, endpoint: str, instance: int) -> None:
        self.write_resource(endpoint, I2C_OBJECT_ID, instance, I2C_RESOURCES["rx_buffer_pos"], 0)

    def i2c_set_rx_size(self, endpoint: str, instance: int, size: int) -> None:
        self.write_resource(endpoint, I2C_OBJECT_ID, instance, I2C_RESOURCES["rx_buffer_size"], size)

    def i2c_write(self, endpoint: str, instance: int, payload: bytes) -> None:
        self.write_resource(endpoint, I2C_OBJECT_ID, instance, I2C_RESOURCES["tx_payload"], payload, is_bytes=True)

    def i2c_read_chunk(self, endpoint: str, instance: int) -> bytes:
        data = self.read_resource(
            endpoint,
            I2C_OBJECT_ID,
            instance,
            I2C_RESOURCES["rx_chunk"],
            headers={"Accept": "application/octet-stream"},
        )

        def _extract_senml_bytes(payload: Any) -> Optional[bytes]:
            if isinstance(payload, list) and payload:
                return _extract_senml_bytes(payload[0])
            if not isinstance(payload, dict):
                return None
            val = payload.get("vd") or payload.get("data")
            if isinstance(val, str):
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
                    val = self._decode_value(parsed)
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


def pick_client(client: Lwm2mRestClient, preferred: Optional[str]) -> str:
    if preferred:
        return preferred
    clients = list(client.list_clients())
    if not clients:
        raise RuntimeError("No LwM2M clients registered")
    if len(clients) > 1:
        raise RuntimeError(f"Multiple clients registered; pick one with --client: {', '.join(clients)}")
    return clients[0]


class I2CSession:
    def __init__(self, client: Lwm2mRestClient, endpoint: str, instance: int = 0):
        self.client = client
        self.endpoint = endpoint
        self.instance = instance

    def open(self, addr: int, *, tx_pin: Optional[int] = None, rx_pin: Optional[int] = None) -> None:
        self.client.i2c_set_open(self.endpoint, self.instance, False)
        if tx_pin is not None:
            self.client.write_resource(self.endpoint, I2C_OBJECT_ID, self.instance, I2C_RESOURCES["tx_pin"], tx_pin)
        if rx_pin is not None:
            self.client.write_resource(self.endpoint, I2C_OBJECT_ID, self.instance, I2C_RESOURCES["rx_pin"], rx_pin)
        self.client.i2c_set_address(self.endpoint, self.instance, addr)
        self.client.i2c_set_open(self.endpoint, self.instance, True)
        self.client.i2c_reset_cursor(self.endpoint, self.instance)
        self.client.i2c_set_rx_size(self.endpoint, self.instance, 16)

    def close(self) -> None:
        self.client.i2c_set_open(self.endpoint, self.instance, False)

    def reset_cursor(self) -> None:
        self.client.i2c_reset_cursor(self.endpoint, self.instance)

    def set_rx_size(self, size: int) -> None:
        self.client.i2c_set_rx_size(self.endpoint, self.instance, size)

    def write(self, payload: Sequence[int]) -> None:
        self.client.i2c_write(self.endpoint, self.instance, bytes(payload))

    def read(self) -> bytes:
        return self.client.i2c_read_chunk(self.endpoint, self.instance)
