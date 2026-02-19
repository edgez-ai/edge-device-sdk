#!/usr/bin/env python3
"""
Collect flow meter data using the RS485 REST API over LwM2M.

This script reads flow rate and total volume from a Modbus flow meter
via the edge device's RS485 interface REST API, enabling remote data
collection without direct serial access.

Usage:
    # Using REST API (default):
    python collect_flow_modbus.py --base-url http://192.168.10.177:8088 --client B43A45A45A08 \
        --tx-pin 17 --rx-pin 18 --baud 9600

    # Using direct serial for comparison:
    python collect_flow_modbus.py --direct --port /dev/cu.usbserial-14320
"""

import argparse
import csv
import os
import sys
import time
import threading
from datetime import datetime, timezone
from typing import Optional, Tuple, List

# REST API imports
from core import Lwm2mRestClient, RestConfig
from core.uart_client import RS485_OBJECT_ID, RS485_RESOURCES, UartSession
from driver import FlowMeter, FlowMeterConfig

# Direct serial imports (optional fallback)
try:
    from pymodbus.client import ModbusSerialClient
    HAS_PYMODBUS = True
except ImportError:
    HAS_PYMODBUS = False


# Global state for continuous reading
records: List[float] = []
last_total_volume: Optional[float] = None
stop_event = threading.Event()

# Default configuration
DEFAULT_POLL_INTERVAL_SEC = 1.0
DEFAULT_BAUD = 9600
DEFAULT_UNIT_ID = 1
DEFAULT_ADDRESS = 0
DEFAULT_COUNT = 4
DEFAULT_FLOW_SCALE = 100000.0
DEFAULT_VOLUME_SCALE = 10000.0


def decode_bcd_32(value: int) -> int:
    """Decode a 32-bit BCD value to integer."""
    return (
        ((value >> 28) & 0xF) * 10000000 +
        ((value >> 24) & 0xF) * 1000000 +
        ((value >> 20) & 0xF) * 100000 +
        ((value >> 16) & 0xF) * 10000 +
        ((value >> 12) & 0xF) * 1000 +
        ((value >> 8) & 0xF) * 100 +
        ((value >> 4) & 0xF) * 10 +
        (value & 0xF)
    )


def read_flow_direct(
    port: str,
    baudrate: int = DEFAULT_BAUD,
    unit_id: int = DEFAULT_UNIT_ID,
    address: int = DEFAULT_ADDRESS,
    count: int = DEFAULT_COUNT,
    flow_scale: float = DEFAULT_FLOW_SCALE,
    volume_scale: float = DEFAULT_VOLUME_SCALE,
) -> Optional[Tuple[float, float]]:
    """Read flow meter data using direct serial connection (pymodbus)."""
    if not HAS_PYMODBUS:
        print("pymodbus not installed. Use: pip install pymodbus")
        return None

    client = ModbusSerialClient(
        port=port,
        baudrate=baudrate,
        parity='N',
        stopbits=1,
        bytesize=8,
        timeout=1
    )

    if not client.connect():
        print("Failed to connect to Modbus device.")
        return None

    try:
        result = client.read_holding_registers(
            address=address,
            count=count,
            slave=unit_id
        )
        if result.isError():
            print("Error reading registers:", result)
            return None

        regs = result.registers
        flow_rate_raw = (regs[2] << 16) | regs[3]
        total_volume_raw = (regs[0] << 16) | regs[1]

        flow_rate = decode_bcd_32(flow_rate_raw) / flow_scale
        total_volume = decode_bcd_32(total_volume_raw) / volume_scale
        return flow_rate, total_volume
    finally:
        client.close()


def create_rest_flow_meter(args: argparse.Namespace) -> Tuple[UartSession, FlowMeter]:
    """Create a FlowMeter instance using the REST API."""
    config = RestConfig(base_url=args.base_url, timeout=args.timeout)
    client = Lwm2mRestClient(config)
    
    # Pick endpoint
    endpoint = args.client
    if not endpoint:
        endpoints = client.endpoints()
        if not endpoints:
            raise RuntimeError("No endpoints available")
        endpoint = endpoints[0]
        print(f"Auto-selected endpoint: {endpoint}")
    
    # Create UART session for RS485
    session = UartSession(
        client,
        endpoint,
        args.instance,
        object_id=RS485_OBJECT_ID,
        resources=RS485_RESOURCES,
        debug=args.debug,
    )
    
    # Open the RS485 interface
    session.open(
        baudrate=args.baud,
        tx_pin=args.tx_pin,
        rx_pin=args.rx_pin,
        rx_size=args.rx_size,
        modbus_unit_id=args.unit_id,
        mode=args.rs485_mode,
    )
    
    # Create flow meter
    meter = FlowMeter(
        session,
        FlowMeterConfig(
            unit_id=args.unit_id,
            register_address=args.address,
            register_count=args.count,
            flow_scale=args.flow_scale,
            volume_scale=args.volume_scale,
            timeout_s=args.modbus_timeout,
        ),
    )
    
    return session, meter


def write_to_csv(flow_records: List[float], total_volume: float) -> None:
    """Write records to a CSV file with moving window."""
    os.makedirs("data", exist_ok=True)
    with open("data/flow.csv", "a", newline="") as csvfile:
        writer = csv.writer(csvfile)
        for i in range(0, len(flow_records) - 25 + 1, 25):
            window = flow_records[i:i + 50]
            if len(window) == 50:
                writer.writerow(window + [total_volume])


def continuous_read_rest(args: argparse.Namespace) -> None:
    """Continuous reading loop using REST API."""
    global records, last_total_volume
    
    try:
        session, meter = create_rest_flow_meter(args)
    except Exception as e:
        print(f"Failed to initialize REST flow meter: {e}")
        return
    
    print(f"Reading flow meter via REST API ({args.base_url})")
    print(f"  Endpoint: {args.client}")
    print(f"  TX Pin: {args.tx_pin}, RX Pin: {args.rx_pin}")
    print(f"  Baudrate: {args.baud}, Unit ID: {args.unit_id}")
    print(f"  Poll interval: {args.poll_interval}s")
    print()
    
    iterations = 0
    try:
        while not stop_event.is_set():
            result = meter.read_flow_and_total_volume()
            timestamp = datetime.now(timezone.utc).isoformat()
            
            if result is None:
                print(f"{timestamp} | ERROR: modbus timeout")
            else:
                flow_rate, total_volume = result
                last_total_volume = total_volume
                records.append(flow_rate)
                
                print(f"{timestamp} | Flow: {flow_rate:.4f} L/h | Volume: {total_volume:.4f} L")
                
                if len(records) >= 50:
                    write_to_csv(records, last_total_volume)
                    records = records[-25:]
            
            iterations += 1
            if args.count_limit > 0 and iterations >= args.count_limit:
                break
            
            time.sleep(args.poll_interval)
    except Exception as e:
        print(f"Error in REST reading loop: {e}")
    finally:
        try:
            session.close()
        except Exception:
            pass


def continuous_read_direct(args: argparse.Namespace) -> None:
    """Continuous reading loop using direct serial."""
    global records, last_total_volume
    
    print(f"Reading flow meter via direct serial ({args.port})")
    print(f"  Baudrate: {args.baud}, Unit ID: {args.unit_id}")
    print(f"  Poll interval: {args.poll_interval}s")
    print()
    
    iterations = 0
    try:
        while not stop_event.is_set():
            result = read_flow_direct(
                port=args.port,
                baudrate=args.baud,
                unit_id=args.unit_id,
                address=args.address,
                count=args.count,
                flow_scale=args.flow_scale,
                volume_scale=args.volume_scale,
            )
            timestamp = datetime.now(timezone.utc).isoformat()
            
            if result is None:
                print(f"{timestamp} | ERROR: modbus read failed")
            else:
                flow_rate, total_volume = result
                last_total_volume = total_volume
                records.append(flow_rate)
                
                print(f"{timestamp} | Flow: {flow_rate:.4f} L/h | Volume: {total_volume:.4f} L")
                
                if len(records) >= 50:
                    write_to_csv(records, last_total_volume)
                    records = records[-25:]
            
            iterations += 1
            if args.count_limit > 0 and iterations >= args.count_limit:
                break
            
            time.sleep(args.poll_interval)
    except Exception as e:
        print(f"Error in direct reading loop: {e}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Collect flow meter data via RS485 REST API or direct serial"
    )
    
    # Mode selection
    parser.add_argument(
        "--direct",
        action="store_true",
        help="Use direct serial connection instead of REST API"
    )
    
    # Direct serial options
    parser.add_argument(
        "--port",
        type=str,
        default="/dev/cu.usbserial-14320",
        help="Serial port for direct mode"
    )
    
    # REST API options
    parser.add_argument(
        "--base-url",
        type=str,
        default="http://192.168.10.177:8088",
        help="LwM2M REST API base URL"
    )
    parser.add_argument(
        "--client",
        type=str,
        default="",
        help="LwM2M client endpoint (auto-select if empty)"
    )
    parser.add_argument(
        "--instance",
        type=int,
        default=0,
        help="LwM2M object instance"
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="REST API timeout"
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug output"
    )
    
    # RS485/UART configuration (defaults match modbus example: TX=17, RX=18)
    parser.add_argument("--tx-pin", type=int, default=17, help="RS485 TX pin (default: 17)")
    parser.add_argument("--rx-pin", type=int, default=18, help="RS485 RX pin (default: 18)")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="Baudrate")
    parser.add_argument("--rx-size", type=int, default=256, help="RX buffer size")
    parser.add_argument("--rs485-mode", type=int, default=0, help="RS485 mode value")
    
    # Modbus configuration
    parser.add_argument("--unit-id", type=int, default=DEFAULT_UNIT_ID, help="Modbus unit ID")
    parser.add_argument("--address", type=int, default=DEFAULT_ADDRESS, help="Register address")
    parser.add_argument("--count", type=int, default=DEFAULT_COUNT, help="Register count")
    parser.add_argument("--flow-scale", type=float, default=DEFAULT_FLOW_SCALE, help="Flow rate scale")
    parser.add_argument("--volume-scale", type=float, default=DEFAULT_VOLUME_SCALE, help="Volume scale")
    parser.add_argument("--modbus-timeout", type=float, default=1.0, help="Modbus read timeout")
    
    # Collection options
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_SEC,
        help="Poll interval in seconds"
    )
    parser.add_argument(
        "--count-limit",
        type=int,
        default=0,
        help="Number of samples (0 for unlimited)"
    )
    
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    
    print("=" * 60)
    print("Flow Meter Data Collection")
    print("=" * 60)
    
    if args.direct:
        if not HAS_PYMODBUS:
            print("ERROR: pymodbus not installed. Install with: pip install pymodbus")
            sys.exit(1)
        read_func = continuous_read_direct
    else:
        read_func = continuous_read_rest
    
    # Run in thread for clean shutdown
    reader_thread = threading.Thread(target=read_func, args=(args,), daemon=True)
    reader_thread.start()
    
    try:
        print("Press Ctrl+C to stop...")
        while reader_thread.is_alive():
            reader_thread.join(timeout=1)
    except KeyboardInterrupt:
        print("\nStopping...")
        stop_event.set()
        reader_thread.join(timeout=5)
    
    print("Exiting.")


if __name__ == "__main__":
    main()

