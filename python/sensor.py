"""
I2C-over-LwM2M helper that drives raw I2C transactions via the custom
I2C interface object (ID 10251) through a Leshan-like REST gateway.

The goal is to keep firmware simple (just exposes the I2C bridge object)
and shift protocol handling (e.g., ENS210 temperature/humidity reads)
into Python.
"""

from __future__ import annotations

import argparse
import base64
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Optional, Sequence
from datetime import datetime

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


# ENS210 register map (matches the C driver)
ENS210_REG_SENS_START = 0x22
ENS210_REG_SENS_STAT = 0x24
ENS210_REG_T_VAL = 0x30
ENS210_REG_H_VAL = 0x33

# SHT3x commands
SHT3X_CMD_RESET = [0x30, 0xA2]  # Soft reset
SHT3X_CMD_SINGLE_SHOT_HIGH = [0x2C, 0x06]  # High repeatability with clock stretching
SHT3X_CMD_SINGLE_SHOT_MED = [0x2C, 0x0D]   # Medium repeatability with clock stretching
SHT3X_CMD_SINGLE_SHOT_LOW = [0x2C, 0x10]   # Low repeatability with clock stretching


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
		# Leshan-style responses often wrap payload under "content"; try to decode it.
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
				# Try base64 decode for opaque values
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

	# Convenience wrappers for I2C bridge resources
	def i2c_set_address(self, endpoint: str, instance: int, addr: int) -> None:
		self.write_resource(endpoint, I2C_OBJECT_ID, instance, I2C_RESOURCES["i2c_address"], addr)

	def i2c_set_open(self, endpoint: str, instance: int, open_state: bool) -> None:
		self.write_resource(endpoint, I2C_OBJECT_ID, instance, I2C_RESOURCES["open_state"], "true" if open_state else "false")

	def i2c_reset_cursor(self, endpoint: str, instance: int) -> None:
		self.write_resource(endpoint, I2C_OBJECT_ID, instance, I2C_RESOURCES["rx_buffer_pos"], 0)

	def i2c_set_rx_size(self, endpoint: str, instance: int, size: int) -> None:
		self.write_resource(endpoint, I2C_OBJECT_ID, instance, I2C_RESOURCES["rx_buffer_size"], size)

	def i2c_write(self, endpoint: str, instance: int, payload: bytes) -> None:
		self.write_resource(endpoint, I2C_OBJECT_ID, instance, I2C_RESOURCES["tx_payload"], payload, is_bytes=True)

	def i2c_read_chunk(self, endpoint: str, instance: int) -> bytes:
		# Prefer octet-stream if the gateway supports it
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
			# If gateway returned JSON/SenML as bytes, try to parse then decode
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
			# Leshan may use value/content/bv keys
			val = data.get("value") if "value" in data else data.get("content") or data.get("bv")
			if isinstance(val, str):
				try:
					return base64.b64decode(val)
				except Exception:
					return val.encode()
			if isinstance(val, (bytes, bytearray)):
				return bytes(val)
		# Fallback: nothing available
		return b""


class I2CSession:
	"""Helper to run I2C transactions via the LwM2M I2C object."""

	def __init__(self, client: Lwm2mRestClient, endpoint: str, instance: int = 0):
		self.client = client
		self.endpoint = endpoint
		self.instance = instance

	def open(self, addr: int) -> None:
		self.client.i2c_set_address(self.endpoint, self.instance, addr)
		self.client.i2c_set_open(self.endpoint, self.instance, True)
		self.client.i2c_reset_cursor(self.endpoint, self.instance)
		self.client.i2c_set_rx_size(self.endpoint, self.instance, 16)

	def reset_cursor(self) -> None:
		self.client.i2c_reset_cursor(self.endpoint, self.instance)

	def set_rx_size(self, size: int) -> None:
		self.client.i2c_set_rx_size(self.endpoint, self.instance, size)

	def write(self, payload: Sequence[int]) -> None:
		self.client.i2c_write(self.endpoint, self.instance, bytes(payload))

	def read(self) -> bytes:
		return self.client.i2c_read_chunk(self.endpoint, self.instance)


def pick_client(client: Lwm2mRestClient, preferred: Optional[str]) -> str:
	if preferred:
		return preferred
	clients = list(client.list_clients())
	if not clients:
		raise RuntimeError("No LwM2M clients registered")
	if len(clients) > 1:
		raise RuntimeError(f"Multiple clients registered; pick one with --client: {', '.join(clients)}")
	return clients[0]


def ens210_read(session: I2CSession, delay_s: float = 0.15) -> Dict[str, float]:
	# We only expect 3-byte reads per register on ENS210
	session.set_rx_size(3)
	session.reset_cursor()
	# Start one-shot measurement: write [SENS_START, 0x03]
	session.write([ENS210_REG_SENS_START, 0x03])
	time.sleep(delay_s)

	# Read temperature (3 bytes)
	session.reset_cursor()
	session.set_rx_size(3)
	session.write([ENS210_REG_T_VAL])
	time.sleep(0.005)
	temp_raw = session.read()

	# Read humidity (3 bytes)
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
		temperature = (t_val / 64.0) - 273.15  # Kelvin -> Celsius
	if h_val is not None:
		humidity = min(max(h_val / 512.0, 0.0), 100.0)

	return {
		"temperature_c": temperature,
		"humidity_rh": humidity,
		"temp_raw": temp_raw.hex(),
		"hum_raw": hum_raw.hex(),
	}


def sht3x_read(session: I2CSession, repeatability: str = "high", delay_s: float = 0.001) -> Dict[str, float]:
    # Reset sensor first
    session.reset_cursor()
    session.write(SHT3X_CMD_RESET)
    time.sleep(0.002)  # Reset time
    
    # Set up commands based on repeatability
    if repeatability == "high":
        cmd = SHT3X_CMD_SINGLE_SHOT_HIGH
    elif repeatability == "med":
        cmd = SHT3X_CMD_SINGLE_SHOT_MED
    else:  # low
        cmd = SHT3X_CMD_SINGLE_SHOT_LOW
    
    # We expect 6 bytes: temp MSB, temp LSB, temp CRC, hum MSB, hum LSB, hum CRC
    session.set_rx_size(6)
    session.reset_cursor()
    
    # Start measurement (with clock stretching, sensor will hold clock until ready)
    session.write(cmd)
    time.sleep(delay_s)  # Minimal delay since clock stretching handles timing
    
    # Read the result (no additional write needed)
    session.reset_cursor()
    session.set_rx_size(6)
    data = session.read()
    
    if len(data) < 6:
        return {
            "temperature_c": None,
            "humidity_rh": None,
            "raw_data_hex": data.hex(),
            "error": "Incomplete data received"
        }
    
    # Parse temperature (first 2 bytes + CRC)
    temp_raw = (data[0] << 8) | data[1]
    temp_crc = data[2]
    
    # Parse humidity (next 2 bytes + CRC)
    hum_raw = (data[3] << 8) | data[4]
    hum_crc = data[5]
    
    # Simple CRC check (SHT3x uses CRC-8)
    def crc8(data: bytes) -> int:
        crc = 0xFF
        for byte in data:
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
        "repeatability": repeatability
    }


def parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Drive I2C via LwM2M REST gateway")
	parser.add_argument("--base-url", default="http://192.168.100.1:8088", help="REST gateway base URL")
	parser.add_argument("--client", help="LwM2M endpoint name; defaults to the only registered client")
	parser.add_argument("--instance", type=int, default=0, help="I2C object instance id")
	parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds")
	parser.add_argument("--verbose", action="store_true", help="Ignored; kept for backward compatibility")

	mode = parser.add_subparsers(dest="mode", required=False)

	# ENS210 mode
	p_ens = mode.add_parser("ens210", help="Read ENS210 temperature/humidity")
	p_ens.add_argument("--addr", type=lambda x: int(x, 0), default=0x43, help="I2C address (default 0x43)")
	p_ens.add_argument("--delay", type=float, default=0.15, help="Measurement wait time seconds")
	p_ens.add_argument("--interval", type=float, default=0.0, help="Poll interval seconds; 0 for one-shot")
	p_ens.add_argument("--count", type=int, default=0, help="Number of polls when interval>0; 0 for forever")

	# SHT3x mode
	p_sht3x = mode.add_parser("sht3x", help="Read SHT3x temperature/humidity")
	p_sht3x.add_argument("--addr", type=lambda x: int(x, 0), default=0x44, help="I2C address (default 0x44)")
	p_sht3x.add_argument("--repeatability", choices=["high", "med", "low"], default="high", help="Measurement repeatability")
	p_sht3x.add_argument("--delay", type=float, default=0.001, help="Measurement wait time seconds")
	p_sht3x.add_argument("--interval", type=float, default=0.0, help="Poll interval seconds; 0 for one-shot")
	p_sht3x.add_argument("--count", type=int, default=0, help="Number of polls when interval>0; 0 for forever")

	# Raw mode
	p_raw = mode.add_parser("raw", help="Manual I2C transaction")
	p_raw.add_argument("--addr", type=lambda x: int(x, 0), required=True, help="I2C address")
	p_raw.add_argument("--write", type=str, required=True, help="Bytes to write, e.g. 0x22,0x03 or 34,3")
	p_raw.add_argument("--read", type=int, default=0, help="Bytes to read after write")

	return parser.parse_args()


def parse_byte_list(text: str) -> Sequence[int]:
	parts = [p.strip() for p in text.split(',') if p.strip()]
	return [int(p, 0) for p in parts]


def main() -> None:
	args = parse_args()
	config = RestConfig(base_url=args.base_url, timeout=args.timeout)
	client = Lwm2mRestClient(config)
	endpoint = pick_client(client, args.client)
	session = I2CSession(client, endpoint, args.instance)

	if args.mode == "raw":
		session.open(args.addr)
		if args.read > 0:
			client.i2c_set_rx_size(endpoint, args.instance, args.read)
		session.write(parse_byte_list(args.write))
		time.sleep(0.01)
		data = b""
		if args.read > 0:
			data = session.read()[: args.read]
		print({"time": datetime.utcnow().isoformat(), "endpoint": endpoint, "raw_read_hex": data.hex(), "raw_read_len": len(data)})
		return

	elif args.mode == "sht3x":
		addr = getattr(args, "addr", 0x44)
		repeatability = getattr(args, "repeatability", "high")
		delay = getattr(args, "delay", 0.001)
		interval = getattr(args, "interval", 0.0)
		count = getattr(args, "count", 0)
		session.open(addr)

		def _once() -> None:
			result = sht3x_read(session, repeatability=repeatability, delay_s=delay)
			print({"time": datetime.utcnow().isoformat(), "endpoint": endpoint, **result})

		if interval > 0:
			iterations = 0
			while True:
				_once()
				iterations += 1
				if count > 0 and iterations >= count:
					break
				time.sleep(interval)
		else:
			_once()

	else:  # Default mode: ENS210
		addr = getattr(args, "addr", 0x43)
		delay = getattr(args, "delay", 0.15)
		interval = getattr(args, "interval", 0.0)
		count = getattr(args, "count", 0)
		session.open(addr)

		def _once() -> None:
			result = ens210_read(session, delay_s=delay)
			print({"time": datetime.utcnow().isoformat(), "endpoint": endpoint, **result})

		if interval > 0:
			iterations = 0
			while True:
				_once()
				iterations += 1
				if count > 0 and iterations >= count:
					break
				time.sleep(interval)
		else:
			_once()


if __name__ == "__main__":
	main()
