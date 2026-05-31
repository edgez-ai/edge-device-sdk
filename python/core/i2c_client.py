from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Sequence
from urllib.parse import urlparse

import requests

DEFAULT_TOKEN_ENDPOINT = "https://www.edgez.ai/api/v1/api-keys/access-token"

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
    access_token: Optional[str] = None
    api_key: Optional[str] = None
    token_endpoint: Optional[str] = None
    token_zone_name: Optional[str] = None

    @staticmethod
    def _infer_zone_name_from_base_url(base_url: str) -> Optional[str]:
        raw = (base_url or "").strip()
        if not raw:
            return None
        parsed = urlparse(raw if "://" in raw else f"https://{raw}")
        hostname = (parsed.hostname or "").strip().lower()
        if not hostname:
            return None
        labels = [label for label in hostname.split(".") if label]
        if len(labels) < 3:
            return None
        # Host format: {peerId}.{zoneName}.{domainSuffix}
        return labels[1]

    def __post_init__(self) -> None:
        if not self.access_token:
            self.access_token = os.getenv("EDGE_RELAY_ACCESS_TOKEN")
        if not self.api_key:
            self.api_key = os.getenv("IOT_DASHBOARD_API_KEY")
        if not self.token_endpoint:
            self.token_endpoint = os.getenv("IOT_DASHBOARD_TOKEN_ENDPOINT") or DEFAULT_TOKEN_ENDPOINT
        if not self.token_zone_name:
            self.token_zone_name = os.getenv("IOT_DASHBOARD_ZONE_NAME")
        if not self.token_zone_name:
            self.token_zone_name = self._infer_zone_name_from_base_url(self.base_url)


class Lwm2mRestClient:
    def __init__(self, config: RestConfig) -> None:
        self.config = config
        self._cached_access_token: Optional[str] = config.access_token
        self._cached_access_token_exp_unix: Optional[int] = None
        if self._cached_access_token:
            self._cached_access_token_exp_unix = self._jwt_exp(self._cached_access_token)

    def _url(self, path: str) -> str:
        return f"{self.config.base_url.rstrip('/')}{path}"

    def _auth_headers(self, headers: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        combined: Dict[str, str] = dict(headers or {})
        token = self._resolve_access_token()
        if token:
            combined["Authorization"] = f"Bearer {token}"
        return combined

    @staticmethod
    def _jwt_exp(token: str) -> Optional[int]:
        parts = token.split(".")
        if len(parts) != 3:
            return None
        payload = parts[1]
        padding = "=" * (-len(payload) % 4)
        try:
            raw = base64.urlsafe_b64decode(payload + padding)
            data = json.loads(raw.decode("utf-8"))
        except Exception:
            return None
        exp = data.get("exp") if isinstance(data, dict) else None
        return int(exp) if isinstance(exp, (int, float)) else None

    def _exchange_api_key_for_access_token(self) -> Optional[str]:
        api_key = (self.config.api_key or "").strip()
        token_endpoint = (self.config.token_endpoint or "").strip()
        zone_name = (self.config.token_zone_name or "").strip()
        if not api_key or not token_endpoint:
            return None
        if not zone_name:
            raise RuntimeError(
                "Cannot infer zoneName from base_url. Expected host format {peerId}.{zoneName}.{domain}."
            )

        resp = requests.post(
            token_endpoint,
            json={"zoneName": zone_name},
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            timeout=self.config.timeout,
        )
        resp.raise_for_status()
        payload = resp.json() if resp.content else {}
        if not isinstance(payload, dict):
            raise RuntimeError("Access token response is not a JSON object")
        token = (
            str(payload.get("relayToken") or "").strip()
            or str(payload.get("accessToken") or "").strip()
            or str(payload.get("token") or "").strip()
        )
        if not token:
            raise RuntimeError("Access token response did not include relayToken/accessToken/token")
        self._cached_access_token = token
        self._cached_access_token_exp_unix = self._jwt_exp(token)
        return token

    def _resolve_access_token(self) -> Optional[str]:
        now = int(time.time())
        # Keep a small safety margin before expiry.
        if (
            self._cached_access_token
            and (
                self._cached_access_token_exp_unix is None
                or self._cached_access_token_exp_unix > now + 60
            )
        ):
            return self._cached_access_token

        return self._exchange_api_key_for_access_token()

    def list_clients(self) -> Iterable[str]:
        url = self._url("/api/clients")
        resp = requests.get(url, headers=self._auth_headers(), timeout=self.config.timeout)
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
        resp = requests.get(url, headers=self._auth_headers(headers), timeout=self.config.timeout)
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
        headers = self._auth_headers(
            {"Content-Type": "application/octet-stream" if is_bytes else "text/plain"}
        )
        data = value if is_bytes else str(value)
        resp = requests.put(url, data=data, headers=headers, timeout=self.config.timeout)
        resp.raise_for_status()

    def execute(self, endpoint: str, obj: int, inst: int, res: int) -> None:
        url = self._url(f"/api/clients/{endpoint}/{obj}/{inst}/{res}")
        resp = requests.post(url, headers=self._auth_headers(), timeout=self.config.timeout)
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

    def set_enabled(self, enabled: bool) -> None:
        self.client.i2c_set_enabled(self.endpoint, self.instance, enabled)

    def enable(self) -> None:
        self.set_enabled(True)

    def close(self) -> None:
        self.client.i2c_set_open(self.endpoint, self.instance, False)

    def disable(self) -> None:
        self.close()
        self.set_enabled(False)

    def reset_cursor(self) -> None:
        self.client.i2c_reset_cursor(self.endpoint, self.instance)

    def set_rx_size(self, size: int) -> None:
        self.client.i2c_set_rx_size(self.endpoint, self.instance, size)

    def write(self, payload: Sequence[int]) -> None:
        self.client.i2c_write(self.endpoint, self.instance, bytes(payload))

    def read(self) -> bytes:
        return self.client.i2c_read_chunk(self.endpoint, self.instance)
