#!/usr/bin/env python3
"""
UART VC0706 Camera Test via REST API

This script mirrors test_rs485_vc0706_rest.py but uses the dedicated UART
REST object instead of RS485.

Usage:
    python test_uart_vc0706_rest.py --client <ENDPOINT> --base-url <URL> [options]

Example:
    python test_uart_vc0706_rest.py --client B43A45A45A2C --base-url http://192.168.10.105:8088
"""

import argparse
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import Lwm2mRestClient, RestConfig
from core.uart_client import UartSession, UART_OBJECT_ID, UART_RESOURCES
from test_rs485_vc0706_rest import DeviceLogPoller, VC0706RestCamera


class VC0706UartRestCamera(VC0706RestCamera):
    """VC0706 camera controller using UART object resources."""

    def __init__(
        self,
        client: Lwm2mRestClient,
        endpoint: str,
        instance: int = 0,
        baudrate: int = 115200,
        tx_pin: int | None = None,
        rx_pin: int | None = None,
        power_on_wait: float = 1.0,
        debug: bool = True,
    ):
        super().__init__(
            client=client,
            endpoint=endpoint,
            instance=instance,
            baudrate=baudrate,
            tx_pin=tx_pin,
            rx_pin=rx_pin,
            power_on_wait=power_on_wait,
            debug=debug,
        )
        self.session = UartSession(
            client=client,
            endpoint=endpoint,
            instance=instance,
            object_id=UART_OBJECT_ID,
            resources=UART_RESOURCES,
            debug=debug,
        )
        self._uart_connected = False

    def connect(self) -> bool:
        if self._uart_connected:
            self._log("UART already open; reusing existing connection")
            return True
        try:
            self._log("Enabling UART interface power...")
            self.session.set_enabled(True)
            if self.power_on_wait > 0:
                self._log(f"Waiting {self.power_on_wait:.1f}s after power enable...")
                time.sleep(self.power_on_wait)
            self._log(f"Opening UART at {self.baudrate} baud...")
            self.session.open(
                baudrate=self.baudrate,
                tx_pin=self.tx_pin,
                rx_pin=self.rx_pin,
                rx_size=4096,
            )
            self._log("Waiting 2.0s for camera stabilization...")
            time.sleep(2.0)
            self._uart_connected = True
            self._log("UART connection opened")
            return True
        except Exception as e:
            self._log(f"Failed to open UART: {e}")
            return False

    def disconnect(self) -> None:
        try:
            if self._uart_connected:
                self.session.close()
            self._uart_connected = False
            self._log("Disabling UART interface power...")
            self.session.set_enabled(False)
            self._log("UART connection closed and power disabled")
        except Exception as e:
            self._log(f"Error closing UART: {e}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Test VC0706 camera via UART REST API")
    parser.add_argument("--client", "-c", required=True, help="LwM2M client endpoint name")
    parser.add_argument(
        "--base-url",
        "-u",
        default="http://192.168.100.1:8088",
        help="Base URL of the LwM2M server REST API",
    )
    parser.add_argument("--instance", "-i", type=int, default=0, help="UART object instance (default: 0)")
    parser.add_argument("--baud", "-b", type=int, default=115200, help="Baud rate (default: 115200)")
    parser.add_argument("--tx-pin", type=int, default=19, help="UART TX pin (default: 19)")
    parser.add_argument("--rx-pin", type=int, default=20, help="UART RX pin (default: 20)")
    parser.add_argument(
        "--action",
        choices=["version", "capture", "set-resolution"],
        default="capture",
        help="Action to run (default: capture)",
    )
    parser.add_argument("--output", "-o", default="capture.jpg", help="Output file for capture")
    parser.add_argument("--reset", action="store_true", help="Reset camera before action")
    parser.add_argument("--quiet", "-q", action="store_true", help="Suppress debug output")
    parser.add_argument("--no-logs", action="store_true", help="Disable device log polling")
    parser.add_argument("--log-interval", type=float, default=0.3, help="Device log poll interval seconds")
    parser.add_argument("--power-on-wait", type=float, default=1.0, help="Delay after enabling power (seconds)")

    args = parser.parse_args()

    print(f"Connecting to {args.base_url} as {args.client}...")
    print(f"Using UART pins TX={args.tx_pin}, RX={args.rx_pin}")

    config = RestConfig(base_url=args.base_url)
    client = Lwm2mRestClient(config)

    log_poller = None
    if not args.no_logs:
        log_poller = DeviceLogPoller(
            client=client,
            endpoint=args.client,
            poll_interval=args.log_interval,
        )

    camera = VC0706UartRestCamera(
        client=client,
        endpoint=args.client,
        instance=args.instance,
        baudrate=args.baud,
        tx_pin=args.tx_pin,
        rx_pin=args.rx_pin,
        power_on_wait=args.power_on_wait,
        debug=not args.quiet,
    )

    try:
        if log_poller:
            print("Starting device log polling...")
            log_poller.start()

        if not camera.connect():
            print("Failed to connect to camera")
            return 1

        if args.reset:
            print("\n--- Resetting Camera ---")
            camera.reset()

        ok = False
        if args.action == "version":
            print("\n--- Getting Camera Version ---")
            version = camera.get_version()
            ok = bool(version)
        elif args.action == "set-resolution":
            print("\n--- Setting Camera Resolution ---")
            ok = camera.set_resolution()
        else:
            if log_poller:
                print("Pausing device log polling for image transfer...")
                log_poller.stop()
                log_poller = None
            print("\n--- Setting Camera Resolution ---")
            if not camera.set_resolution():
                print("\n✗ Failed to set camera resolution before capture")
                return 1
            print("\n--- Capturing Image ---")
            image = camera.capture_image()
            ok = bool(image) and camera.save_image(image, args.output)

        if log_poller:
            log_poller.fetch_once()

        if ok:
            if args.action == "version":
                print("\n✓ Camera version retrieved successfully")
            elif args.action == "set-resolution":
                print("\n✓ Camera resolution command sent successfully")
            else:
                print("\n✓ Camera image captured successfully")
            return 0

        if args.action == "version":
            print("\n✗ Failed to get camera version")
        elif args.action == "set-resolution":
            print("\n✗ Failed to set camera resolution")
        else:
            print("\n✗ Failed to capture camera image")
        return 1

    except KeyboardInterrupt:
        print("\nInterrupted by user")
        return 1
    except Exception as e:
        print(f"\nError: {e}")
        import traceback

        traceback.print_exc()
        return 1
    finally:
        if log_poller:
            log_poller.stop()
        camera.disconnect()


if __name__ == "__main__":
    sys.exit(main())
