#!/usr/bin/env python3
"""
RS485 VC0706 Camera Test via REST API

This script tests communication with a VC0706 camera through the RS485
REST API interface on an ESP32 device. It sends the GET_VERSION command
and reads the response.

Usage:
    python test_rs485_vc0706_rest.py --client <ENDPOINT> --base-url <URL> [options]

Example:
    python test_rs485_vc0706_rest.py --client B43A45A45A08 --base-url http://192.168.10.177:8088
"""

import argparse
import sys
import time
import threading
from typing import Optional

from core.i2c_client import Lwm2mRestClient, RestConfig
from core.uart_client import UartSession, RS485_RESOURCES


# LwM2M Log Object (from object_log.h)
LOG_OBJECT_ID = 10260
RES_LOG_LINES = 0
RES_LOG_CLEAR = 1
RES_LOG_DROPPED = 2
RES_LOG_PENDING = 3


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
    
    def start(self):
        """Start the log polling thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._thread.start()
    
    def stop(self):
        """Stop the log polling thread."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=2.0)
            self._thread = None
    
    def _poll_loop(self):
        """Background thread that polls for logs."""
        while self._running:
            try:
                self._fetch_and_print_logs()
            except Exception:
                pass  # Ignore errors in background thread
            time.sleep(self.poll_interval)
    
    def _fetch_and_print_logs(self):
        """Fetch logs from device and print them."""
        try:
            data = self.client.read_resource(
                self.endpoint, LOG_OBJECT_ID, self.instance, RES_LOG_LINES
            )
            if data:
                # Handle different response formats
                log_text = None
                if isinstance(data, bytes):
                    log_text = data.decode('utf-8', errors='replace')
                elif isinstance(data, str):
                    log_text = data
                elif isinstance(data, dict):
                    log_text = data.get('value') or data.get('vd') or str(data)
                
                if log_text and log_text.strip():
                    for line in log_text.strip().split('\n'):
                        if line.strip():
                            print(f"\033[36m[ESP32] {line}\033[0m", flush=True)
        except Exception:
            pass  # Silently ignore errors
    
    def fetch_once(self):
        """Fetch and print logs once (blocking)."""
        self._fetch_and_print_logs()


class VC0706RestCamera:
    """VC0706 Camera Controller via RS485 REST API"""
    
    # Protocol constants
    SERIAL_NUM = 0x00  # Camera serial number (usually 0x00)
    
    # Command codes
    CMD_GET_VERSION = 0x11
    CMD_RESET = 0x26
    CMD_FBUF_CTRL = 0x36
    CMD_GET_FBUF_LEN = 0x34
    CMD_READ_FBUF = 0x32
    
    # Frame buffer control types
    FBUF_STOP_FRAME = 0x00
    FBUF_RESUME_FRAME = 0x02
    
    def __init__(
        self,
        client: Lwm2mRestClient,
        endpoint: str,
        instance: int = 0,
        baudrate: int = 115200,
        tx_pin: Optional[int] = None,
        rx_pin: Optional[int] = None,
        debug: bool = True,
    ):
        """
        Initialize VC0706 camera connection via RS485 REST API.
        
        Args:
            client: LwM2M REST client
            endpoint: Device endpoint name
            instance: RS485 object instance (default: 0)
            baudrate: Baud rate (default: 38400)
            tx_pin: TX pin number (optional)
            rx_pin: RX pin number (optional)
            debug: Enable debug output
        """
        self.session = UartSession(
            client=client,
            endpoint=endpoint,
            instance=instance,
            debug=debug,
        )
        self.baudrate = baudrate
        self.tx_pin = tx_pin
        self.rx_pin = rx_pin
        self.debug = debug
        
    def _log(self, msg: str) -> None:
        if self.debug:
            print(f"[VC0706] {msg}", file=sys.stderr, flush=True)
    
    def connect(self) -> bool:
        """
        Open the RS485 connection to the camera.
        
        Returns:
            True if connection opened successfully
        """
        try:
            self._log(f"Opening RS485 at {self.baudrate} baud...")
            self.session.open(
                baudrate=self.baudrate,
                tx_pin=self.tx_pin,
                rx_pin=self.rx_pin,
                rx_size=4096,  # Large buffer for image data
            )
            time.sleep(0.3)  # Wait for connection to stabilize
            self._log("RS485 connection opened")
            return True
        except Exception as e:
            self._log(f"Failed to open RS485: {e}")
            return False
    
    def disconnect(self) -> None:
        """Close the RS485 connection."""
        try:
            self.session.close()
            self._log("RS485 connection closed")
        except Exception as e:
            self._log(f"Error closing RS485: {e}")
    
    def _build_command(self, cmd: int, args: bytes = b'') -> bytes:
        """
        Build a VC0706 command packet.
        
        Args:
            cmd: Command code
            args: Command arguments
            
        Returns:
            Complete command packet
        """
        return bytes([0x56, self.SERIAL_NUM, cmd, len(args)]) + args
    
    def _send_command(self, cmd: int, args: bytes = b'', label: str = "") -> bool:
        """
        Send a command to the camera.
        
        Args:
            cmd: Command code
            args: Command arguments
            label: Description for logging
            
        Returns:
            True if command sent successfully
        """
        packet = self._build_command(cmd, args)
        
        if label:
            self._log(f"TX {label}: {packet.hex()}")
        
        try:
            # Reset cursor before sending to clear any old data
            self.session.reset_cursor()
            self.session.write(packet)
            return True
        except Exception as e:
            self._log(f"Error sending command: {e}")
            return False
    
    def _read_response(self, timeout: float = 2.0, max_len: int = 256) -> bytes:
        """
        Read response from camera with continuous polling.
        Matches the behavior of rs485_vc0706_camera._read_until_timeout()
        
        Args:
            timeout: Total timeout in seconds
            max_len: Maximum bytes to read
            
        Returns:
            Response bytes
        """
        data = b''
        start_time = time.time()
        poll_interval = 0.05  # Poll every 50ms (similar to 10ms in direct serial)
        no_data_count = 0
        
        self._log(f"Polling for response (timeout={timeout}s, max_len={max_len})...")
        
        while len(data) < max_len and (time.time() - start_time) < timeout:
            chunk = self.session.read()
            if chunk:
                data += chunk
                no_data_count = 0
                self._log(f"  Got {len(chunk)} bytes, total: {len(data)} bytes")
            else:
                no_data_count += 1
                # If we have some data and haven't received anything for a while, we might be done
                if data and no_data_count >= 10:  # 10 * 50ms = 500ms of no data
                    self._log(f"  No more data after {no_data_count} polls, stopping")
                    break
                time.sleep(poll_interval)
        
        if data:
            self._log(f"RX (hex): {data.hex()}")
            self._log(f"RX (ascii): {data.decode('ascii', errors='replace')}")
        else:
            self._log("No response received")
        
        return data
    
    def drain_buffer(self, timeout: float = 0.3) -> int:
        """
        Drain any stray data in the receive buffer.
        
        Args:
            timeout: How long to wait for data
            
        Returns:
            Number of bytes drained
        """
        drained = 0
        start = time.time()
        while (time.time() - start) < timeout:
            chunk = self.session.read()
            if chunk:
                drained += len(chunk)
            else:
                time.sleep(0.05)
        
        if drained > 0:
            self._log(f"Drained {drained} stray bytes")
        
        return drained
    
    def _verify_response(self, response: bytes, cmd: int) -> bool:
        """
        Verify response is valid for given command.
        
        Args:
            response: Response bytes
            cmd: Expected command code
            
        Returns:
            True if response is valid
        """
        if len(response) < 4:
            return False
        return (response[0] == 0x76 and 
                response[1] == self.SERIAL_NUM and 
                response[2] == cmd and 
                response[3] == 0x00)
    
    def get_version(self) -> Optional[str]:
        """
        Get camera firmware version.
        
        Returns:
            Version string or None if failed
        """
        self._log("Getting camera version...")
        
        # Drain any stray data first (like original script does)
        self.drain_buffer(0.1)
        
        # Command: 56 00 11 00
        if not self._send_command(self.CMD_GET_VERSION, label="GET_VERSION"):
            return None
        
        # Response: 76 00 11 00 [version string]
        # PTC2M0 cameras return longer ASCII responses, so read more
        response = self._read_response(timeout=2.0, max_len=256)
        
        if len(response) >= 5 and self._verify_response(response, self.CMD_GET_VERSION):
            # Version string follows the header
            version = response[5:].decode('ascii', errors='ignore').strip('\x00\r\n')
            print(f"Camera version: {version}")
            return version
        
        # Try to parse as plain ASCII (for PTC2M0 and similar cameras)
        try:
            ascii_resp = response.decode('ascii', errors='ignore')
            if 'Version' in ascii_resp or 'PTC' in ascii_resp or 'VC0706' in ascii_resp:
                print(f"Camera info (ASCII):\n{ascii_resp}")
                return ascii_resp.strip()
        except:
            pass
        
        if response:
            print(f"Unknown response (hex): {response.hex()}")
            print(f"Unknown response (ascii): {response.decode('ascii', errors='replace')}")
        else:
            print("No response from camera")
        
        return None
    
    def reset(self) -> bool:
        """
        Reset the camera.
        
        Returns:
            True if reset successful
        """
        self._log("Resetting camera...")
        
        # Command: 56 00 26 00
        if not self._send_command(self.CMD_RESET, label="RESET"):
            return False
        
        # Wait for reset to complete
        time.sleep(0.5)
        
        # Read acknowledgment
        response = self._read_response(timeout=3.0)
        
        if len(response) >= 4:
            self._log(f"Reset response: {response[:min(16, len(response))].hex()}")
            # After reset, camera sends "Init end\r\n" string
            time.sleep(2.0)  # Give camera time to reboot
            return True
        
        self._log("Reset: No response")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Test VC0706 camera via RS485 REST API"
    )
    parser.add_argument(
        "--client", "-c",
        required=True,
        help="LwM2M client endpoint name (e.g., B43A45A45A08)"
    )
    parser.add_argument(
        "--base-url", "-u",
        default="http://192.168.100.1:8088",
        help="Base URL of the LwM2M server REST API"
    )
    parser.add_argument(
        "--instance", "-i",
        type=int,
        default=0,
        help="RS485 object instance (default: 0)"
    )
    parser.add_argument(
        "--baud", "-b",
        type=int,
        default=115200,
        help="Baud rate (default: 115200)"
    )
    parser.add_argument(
        "--tx-pin",
        type=int,
        default=None,
        help="TX pin number (optional)"
    )
    parser.add_argument(
        "--rx-pin",
        type=int,
        default=None,
        help="RX pin number (optional)"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset camera before getting version"
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Suppress debug output"
    )
    parser.add_argument(
        "--no-logs",
        action="store_true",
        help="Disable device log polling"
    )
    parser.add_argument(
        "--log-interval",
        type=float,
        default=0.3,
        help="Device log poll interval in seconds (default: 0.3)"
    )
    
    args = parser.parse_args()
    
    print(f"Connecting to {args.base_url} as {args.client}...")
    
    # Create LwM2M REST client
    config = RestConfig(base_url=args.base_url)
    client = Lwm2mRestClient(config)
    
    # Create device log poller
    log_poller = None
    if not args.no_logs:
        log_poller = DeviceLogPoller(
            client=client,
            endpoint=args.client,
            poll_interval=args.log_interval,
        )
    
    # Create camera controller
    camera = VC0706RestCamera(
        client=client,
        endpoint=args.client,
        instance=args.instance,
        baudrate=args.baud,
        tx_pin=args.tx_pin,
        rx_pin=args.rx_pin,
        debug=not args.quiet,
    )
    
    try:
        # Start log polling
        if log_poller:
            print("Starting device log polling...")
            log_poller.start()
            time.sleep(0.2)  # Let it fetch initial logs
        
        # Connect
        if not camera.connect():
            print("Failed to connect to camera")
            return 1
        
        # Reset if requested
        if args.reset:
            print("\n--- Resetting Camera ---")
            camera.reset()
            time.sleep(2.0)
        
        # Get version
        print("\n--- Getting Camera Version ---")
        version = camera.get_version()
        
        # Fetch any remaining logs
        if log_poller:
            time.sleep(0.5)
            log_poller.fetch_once()
        
        if version:
            print(f"\n✓ Camera version retrieved successfully")
            return 0
        else:
            print(f"\n✗ Failed to get camera version")
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
