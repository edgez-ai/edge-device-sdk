#!/usr/bin/env python3
"""
Modbus RTU Test via RS485 REST API

This script follows the same structure as test_rs485_vc0706_rest.py, but targets
Modbus RTU devices connected over RS485. It can read holding registers once,
decode environmental values (humidity/temperature/CO2/light) from the manual
register map, decode flow-meter values like collect_flow_modbus.py, or poll
continuously.

Usage:
	python modbus_rtu_rest.py --client <ENDPOINT> --base-url <URL> [options]

Example:
	python3 modbus_rtu_rest.py --client B43A45A45A08 --base-url http://192.168.10.105:8088 --action flow
"""

import argparse
import sys
import threading
import time
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
	sys.path.insert(0, str(PROJECT_ROOT))

from core import Lwm2mRestClient, RestConfig
from core.uart_client import RS485_RESOURCES, UartSession
from driver import FlowMeter


def _parse_int_list(text: str) -> list[int]:
	values: list[int] = []
	for part in text.split(","):
		part = part.strip()
		if not part:
			continue
		values.append(int(part, 0))
	return values


# LwM2M Log Object (from object_log.h)
LOG_OBJECT_ID = 10260
RES_LOG_LINES = 0


class DeviceLogPoller:
	"""Polls device logs from the LwM2M log object and displays them."""

	def __init__(
		self,
		client: Lwm2mRestClient,
		endpoint: str,
		instance: int = 0,
		poll_interval: float = 0.5,
	):
		self.client = client
		self.endpoint = endpoint
		self.instance = instance
		self.poll_interval = poll_interval
		self._running = False
		self._thread: Optional[threading.Thread] = None

	def start(self) -> None:
		if self._running:
			return
		self._running = True
		self._thread = threading.Thread(target=self._poll_loop, daemon=True)
		self._thread.start()

	def stop(self) -> None:
		self._running = False
		if self._thread:
			self._thread.join(timeout=2.0)
			self._thread = None

	def _poll_loop(self) -> None:
		while self._running:
			try:
				self._fetch_and_print_logs()
			except Exception:
				pass
			time.sleep(self.poll_interval)

	def _fetch_and_print_logs(self) -> None:
		try:
			data = self.client.read_resource(
				self.endpoint, LOG_OBJECT_ID, self.instance, RES_LOG_LINES
			)
			if not data:
				return

			log_text = None
			if isinstance(data, bytes):
				log_text = data.decode("utf-8", errors="replace")
			elif isinstance(data, str):
				log_text = data
			elif isinstance(data, dict):
				log_text = data.get("value") or data.get("vd") or str(data)

			if log_text and log_text.strip():
				for line in log_text.strip().split("\n"):
					if line.strip():
						print(f"\033[36m[ESP32] {line}\033[0m", flush=True)
		except Exception:
			pass

	def fetch_once(self) -> None:
		self._fetch_and_print_logs()


class ModbusRtuRestReader:
	"""Modbus RTU reader over RS485 REST API."""

	def __init__(
		self,
		client: Lwm2mRestClient,
		endpoint: str,
		instance: int = 0,
		baudrate: int = 4800,
		tx_pin: Optional[int] = None,
		rx_pin: Optional[int] = None,
		unit_id: int = 1,
		rs485_mode: int = 0,
		rx_size: int = 256,
		modbus_timeout: float = 1.0,
		debug: bool = True,
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
		self.modbus_timeout = modbus_timeout
		self.debug = debug

	def _log(self, msg: str) -> None:
		if self.debug:
			print(f"[ModbusRTU] {msg}", file=sys.stderr, flush=True)

	def connect(self) -> bool:
		try:
			self._log(f"Opening RS485 at {self.baudrate} baud (unit={self.unit_id})...")
			self.session.open(
				baudrate=self.baudrate,
				tx_pin=self.tx_pin,
				rx_pin=self.rx_pin,
				rx_size=self.rx_size,
				modbus_unit_id=self.unit_id,
				mode=self.rs485_mode,
			)
			self._log("RS485 connection opened")
			return True
		except Exception as exc:
			self._log(f"Failed to open RS485: {exc}")
			return False

	def disconnect(self) -> None:
		try:
			self.session.close()
			self._log("RS485 connection closed")
		except Exception as exc:
			self._log(f"Error closing RS485: {exc}")

	def read_holding_registers(self, address: int, count: int) -> Optional[list[int]]:
		request = FlowMeter.build_read_holding_request(self.unit_id, address, count)
		self._log(f"TX Read Holding Registers: {request.hex()}")

		self.session.reset_cursor()
		self.session.write(request)

		expected_bytes = count * 2
		frame = None
		buffer = b""
		deadline = time.time() + self.modbus_timeout

		while time.time() < deadline:
			chunk = self.session.read()
			if chunk:
				buffer += chunk
				frame = FlowMeter.extract_frame(buffer, self.unit_id, 0x03, expected_bytes)
				if frame:
					break
			time.sleep(0.02)

		if not frame:
			self._log("No valid Modbus response frame received")
			return None

		payload = frame[3:-2]
		regs: list[int] = []
		for i in range(0, len(payload), 2):
			regs.append((payload[i] << 8) | payload[i + 1])

		self._log(f"RX Registers: {[hex(v) for v in regs]}")
		return regs

	def read_env_values(
		self,
		address: int = 0,
		count: int = 5,
	) -> Optional[dict]:
		regs = self.read_holding_registers(address, count)
		if not regs or len(regs) < 3:
			return None

		humidity_raw = regs[0]
		temperature_raw = regs[1]
		co2_ppm = regs[2]

		if temperature_raw >= 0x8000:
			temperature_raw -= 0x10000

		result = {
			"humidity_rh": humidity_raw / 10.0,
			"temperature_c": temperature_raw / 10.0,
			"co2_ppm": co2_ppm,
		}

		if len(regs) >= 5:
			result["light_lux"] = ((regs[3] & 0xFFFF) << 16) | (regs[4] & 0xFFFF)
		elif len(regs) >= 4:
			result["light_lux"] = regs[3]

		return result

	def read_flow_values(
		self,
		address: int = 0,
		count: int = 4,
		flow_scale: float = 100000.0,
		volume_scale: float = 10000.0,
	) -> Optional[dict]:
		"""Read flow meter values using the same register decode as collect_flow_modbus.py."""
		regs = self.read_holding_registers(address, count)
		if not regs or len(regs) < 4:
			return None

		flow_rate_raw = (regs[2] << 16) | regs[3]
		total_volume_raw = (regs[0] << 16) | regs[1]

		flow_rate = FlowMeter.decode_bcd_32(flow_rate_raw) / flow_scale
		total_volume = FlowMeter.decode_bcd_32(total_volume_raw) / volume_scale
		return {
			"flow_rate": flow_rate,
			"total_volume": total_volume,
		}

	def read_device_config(self) -> Optional[dict]:
		"""Read manual config registers: 0x07D0 (addr), 0x07D1 (baud code)."""
		regs = self.read_holding_registers(0x07D0, 2)
		if not regs or len(regs) < 2:
			return None

		addr = regs[0]
		baud_code = regs[1]
		baud_map = {0: 2400, 1: 4800, 2: 9600}
		return {
			"device_address": addr,
			"baud_code": baud_code,
			"baud_rate": baud_map.get(baud_code),
		}

	def probe_device(self) -> Optional[dict]:
		"""Try a few manual-defined register probes to detect any valid Modbus reply."""
		cfg = self.read_device_config()
		if cfg:
			return {"probe": "config_07d0", "data": cfg}

		co2_regs = self.read_holding_registers(0x0002, 1)
		if co2_regs and len(co2_regs) >= 1:
			return {"probe": "co2_0002", "data": {"co2_ppm": co2_regs[0]}}

		env_regs = self.read_holding_registers(0x0000, 5)
		if env_regs and len(env_regs) >= 3:
			return {"probe": "env_0000", "data": {"registers": env_regs}}

		return None


def main() -> int:
	parser = argparse.ArgumentParser(
		description="Test Modbus RTU device via RS485 REST API"
	)
	parser.add_argument(
		"--client",
		"-c",
		required=True,
		help="LwM2M client endpoint name (e.g., B43A45A45A08)",
	)
	parser.add_argument(
		"--base-url",
		"-u",
		default="http://192.168.10.177:8088",
		help="Base URL of the LwM2M server REST API",
	)
	parser.add_argument(
		"--instance",
		"-i",
		type=int,
		default=0,
		help="RS485 object instance (default: 0)",
	)
	parser.add_argument(
		"--baud",
		"-b",
		type=int,
		default=4800,
		help="Baud rate (default: 4800)",
	)
	parser.add_argument("--tx-pin", type=int, default=None, help="TX pin (optional; default uses device setting)")
	parser.add_argument("--rx-pin", type=int, default=None, help="RX pin (optional; default uses device setting)")
	parser.add_argument("--unit-id", type=int, default=1, help="Modbus unit ID")
	parser.add_argument("--address", type=int, default=0, help="Register start address")
	parser.add_argument("--count", type=int, default=5, help="Register count (manual full read uses 5)")
	parser.add_argument("--rx-size", type=int, default=256, help="RX buffer size")
	parser.add_argument(
		"--rs485-mode",
		type=int,
		default=0,
		help="RS485 mode resource value (default: 0)",
	)
	parser.add_argument(
		"--modbus-timeout",
		type=float,
		default=1.0,
		help="Modbus read timeout in seconds",
	)
	parser.add_argument(
		"--action",
		choices=["raw", "env", "flow", "poll", "flow-poll", "scan"],
		default="env",
		help="Action to run (default: env)",
	)
	parser.add_argument(
		"--poll-interval",
		type=float,
		default=1.0,
		help="Polling interval for --action poll",
	)
	parser.add_argument(
		"--samples",
		type=int,
		default=0,
		help="Number of samples for --action poll (0 = unlimited)",
	)
	parser.add_argument(
		"--flow-scale",
		type=float,
		default=100000.0,
		help="Flow rate scale (used by --action flow and --action flow-poll)",
	)
	parser.add_argument(
		"--volume-scale",
		type=float,
		default=10000.0,
		help="Volume scale (used by --action flow and --action flow-poll)",
	)
	parser.add_argument("--quiet", "-q", action="store_true", help="Suppress debug output")
	parser.add_argument("--no-logs", action="store_true", help="Disable device log polling")
	parser.add_argument(
		"--log-interval",
		type=float,
		default=0.3,
		help="Device log poll interval in seconds",
	)
	parser.add_argument(
		"--scan-min-unit",
		type=int,
		default=1,
		help="Minimum Modbus unit id for --action scan",
	)
	parser.add_argument(
		"--scan-max-unit",
		type=int,
		default=10,
		help="Maximum Modbus unit id for --action scan",
	)
	parser.add_argument(
		"--scan-baud-list",
		type=str,
		default="4800,9600,2400",
		help="Comma-separated baud list for --action scan",
	)

	args = parser.parse_args()

	print(f"Connecting to {args.base_url} as {args.client}...")

	config = RestConfig(base_url=args.base_url)
	client = Lwm2mRestClient(config)

	log_poller = None
	if not args.no_logs:
		log_poller = DeviceLogPoller(
			client=client,
			endpoint=args.client,
			poll_interval=args.log_interval,
		)

	reader = ModbusRtuRestReader(
		client=client,
		endpoint=args.client,
		instance=args.instance,
		baudrate=args.baud,
		tx_pin=args.tx_pin,
		rx_pin=args.rx_pin,
		unit_id=args.unit_id,
		rs485_mode=args.rs485_mode,
		rx_size=args.rx_size,
		modbus_timeout=args.modbus_timeout,
		debug=not args.quiet,
	)

	try:
		if log_poller:
			print("Starting device log polling...")
			log_poller.start()

		if args.action != "scan":
			if not reader.connect():
				print("Failed to connect to Modbus device")
				return 1

		if args.action == "raw":
			print("\n--- Reading Holding Registers (raw) ---")
			regs = reader.read_holding_registers(args.address, args.count)
			if not regs:
				print("\n✗ Failed to read holding registers")
				return 1
			print(f"Registers: {regs}")
			print(f"Hex: {[hex(v) for v in regs]}")
			print("\n✓ Register read successful")
			return 0

		if args.action == "scan":
			print("\n--- Scanning Modbus Device (manual cfg registers 0x07D0..0x07D1) ---")
			try:
				baud_candidates = _parse_int_list(args.scan_baud_list)
			except Exception:
				print("\n✗ Invalid --scan-baud-list, expected like: 4800,9600,2400")
				return 1

			if not baud_candidates:
				print("\n✗ Empty --scan-baud-list")
				return 1

			unit_min = min(args.scan_min_unit, args.scan_max_unit)
			unit_max = max(args.scan_min_unit, args.scan_max_unit)

			for unit in range(unit_min, unit_max + 1):
				for baud in baud_candidates:
					print(f"Trying baud={baud}, unit_id={unit}...")
					reader.disconnect()
					reader.baudrate = baud
					reader.unit_id = unit
					if not reader.connect():
						continue
					time.sleep(0.12)
					probe = reader.probe_device()
					if probe:
						print(f"Found device response at baud={baud}, unit_id={unit}")
						print(f"Probe source: {probe['probe']}")
						if probe["probe"] == "config_07d0":
							cfg = probe["data"]
							print(f"Device config addr reg: {cfg['device_address']}")
							if cfg.get("baud_rate") is not None:
								print(f"Device config baud reg: code={cfg['baud_code']} ({cfg['baud_rate']} bps)")
							else:
								print(f"Device config baud reg: code={cfg['baud_code']} (unknown mapping)")
						else:
							print(f"Probe data: {probe['data']}")
						print("\n✓ Scan successful")
						return 0

			print("\n✗ No response for scanned baud/unit combinations")
			print("  Tips: verify 10-30V power, swap 485-A/485-B, and check bus termination")
			return 1

		if args.action == "env":
			print("\n--- Reading Environmental Values (manual register map) ---")
			result = reader.read_env_values(
				address=args.address,
				count=args.count,
			)
			if not result:
				print("\n✗ Failed to read/decode environmental values")
				return 1
			print(f"Humidity: {result['humidity_rh']:.1f} %RH")
			print(f"Temperature: {result['temperature_c']:.1f} °C")
			print(f"CO2: {result['co2_ppm']} ppm")
			if "light_lux" in result:
				print(f"Light: {result['light_lux']} Lux")
			print("\n✓ Environmental read successful")
			return 0

		if args.action == "flow":
			print("\n--- Reading Flow Meter Values (collect_flow_modbus profile) ---")
			flow_count = args.count if args.count != 5 else 4
			result = reader.read_flow_values(
				address=args.address,
				count=flow_count,
				flow_scale=args.flow_scale,
				volume_scale=args.volume_scale,
			)
			if not result:
				print("\n✗ Failed to read/decode flow meter values")
				return 1
			print(f"Flow: {result['flow_rate']:.4f} L/h")
			print(f"Volume: {result['total_volume']:.4f} L")
			print("\n✓ Flow meter read successful")
			return 0

		if args.action == "flow-poll":
			print("\n--- Polling Flow Meter Values (collect_flow_modbus profile) ---")
			sample_count = 0
			flow_count = args.count if args.count != 5 else 4
			while True:
				result = reader.read_flow_values(
					address=args.address,
					count=flow_count,
					flow_scale=args.flow_scale,
					volume_scale=args.volume_scale,
				)
				ts = time.strftime("%Y-%m-%d %H:%M:%S")
				if result:
					print(
						f"{ts} | Flow: {result['flow_rate']:.4f} L/h | Volume: {result['total_volume']:.4f} L"
					)
				else:
					print(f"{ts} | ERROR: modbus timeout / invalid frame")

				sample_count += 1
				if args.samples > 0 and sample_count >= args.samples:
					break
				time.sleep(args.poll_interval)

			print("\n✓ Flow polling completed")
			return 0

		print("\n--- Polling Environmental Values (manual register map) ---")
		sample_count = 0
		while True:
			result = reader.read_env_values(
				address=args.address,
				count=args.count,
			)
			ts = time.strftime("%Y-%m-%d %H:%M:%S")
			if result:
				parts = [
					f"H: {result['humidity_rh']:.1f}%RH",
					f"T: {result['temperature_c']:.1f}°C",
					f"CO2: {result['co2_ppm']}ppm",
				]
				if "light_lux" in result:
					parts.append(f"Lux: {result['light_lux']}")
				print(f"{ts} | " + " | ".join(parts))
			else:
				print(f"{ts} | ERROR: modbus timeout / invalid frame")

			sample_count += 1
			if args.samples > 0 and sample_count >= args.samples:
				break
			time.sleep(args.poll_interval)

		print("\n✓ Polling completed")
		return 0
	except KeyboardInterrupt:
		print("\nInterrupted by user")
		return 1
	except Exception as exc:
		print(f"\nError: {exc}")
		import traceback

		traceback.print_exc()
		return 1
	finally:
		if log_poller:
			log_poller.stop()
		reader.disconnect()


if __name__ == "__main__":
	sys.exit(main())
