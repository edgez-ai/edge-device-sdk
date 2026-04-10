#!/usr/bin/env python3
"""
UART VC0706 Camera Test via REST API

This script tests communication with a VC0706 camera through the UART
REST API interface on an ESP32 device. It sends the GET_VERSION command
and reads the response.

Usage:
    python test_uart_vc0706_rest1.py --client <ENDPOINT> --base-url <URL> [options]

Example:
    python test_uart_vc0706_rest1.py --client B43A45A45A08 --base-url http://192.168.10.177:8088
"""

import argparse
import sys
import time
import threading
from pathlib import Path
from typing import Optional

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import Lwm2mRestClient, RestConfig
from core.uart_client import UartSession, UART_OBJECT_ID, UART_RESOURCES


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
    """VC0706 Camera Controller via UART REST API"""
    
    # Protocol constants
    SERIAL_NUM = 0x00  # Camera serial number (usually 0x00)
    
    # Command codes
    CMD_GET_VERSION = 0x11
    CMD_RESET = 0x26
    CMD_SET_DOWNSIZE = 0x31
    CMD_FBUF_CTRL = 0x36
    CMD_GET_FBUF_LEN = 0x34
    CMD_READ_FBUF = 0x32
    
    # Frame buffer control types
    FBUF_STOP_FRAME = 0x00
    FBUF_RESUME_FRAME = 0x02

    # Capture limits
    READ_CHUNK_SIZE = 256
    MAX_FRAME_SIZE = 500000
    COMMAND_INTERVAL_S = 0.2
    CONTROL_MAX_POLLS = 3
    
    def __init__(
        self,
        client: Lwm2mRestClient,
        endpoint: str,
        instance: int = 0,
        baudrate: int = 921600,
        tx_pin: Optional[int] = None,
        rx_pin: Optional[int] = None,
        power_on_wait: float = 10.0,
        debug: bool = True,
    ):
        """
        Initialize VC0706 camera connection via UART REST API.
        
        Args:
            client: LwM2M REST client
            endpoint: Device endpoint name
            instance: UART object instance (default: 0)
            baudrate: Baud rate (default: 921600)
            tx_pin: TX pin number (optional)
            rx_pin: RX pin number (optional)
            power_on_wait: Delay after enabling interface power before open
            debug: Enable debug output
        """
        self.session = UartSession(
            client=client,
            endpoint=endpoint,
            instance=instance,
            object_id=UART_OBJECT_ID,
            resources=UART_RESOURCES,
            debug=debug,
        )
        self.baudrate = baudrate
        self.tx_pin = tx_pin
        self.rx_pin = rx_pin
        self.power_on_wait = max(0.0, float(power_on_wait))
        self.debug = debug
        
    def _log(self, msg: str) -> None:
        if self.debug:
            print(f"[VC0706] {msg}", file=sys.stderr, flush=True)
    
    def connect(self) -> bool:
        """
        Open the UART connection to the camera.
        
        Returns:
            True if connection opened successfully
        """
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
                rx_size=4096,  # Large buffer for image data
            )
            self._log("UART connection opened")
            return True
        except Exception as e:
            self._log(f"Failed to open UART: {e}")
            return False
    
    def disconnect(self) -> None:
        """Close the UART connection."""
        try:
            self.session.close()
            self._log("Disabling UART interface power...")
            self.session.set_enabled(False)
            self._log("UART connection closed and power disabled")
        except Exception as e:
            self._log(f"Error closing UART: {e}")
    
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
    
    def _send_command(
        self,
        cmd: int,
        args: bytes = b'',
        label: str = "",
        post_write_delay: float | None = None,
    ) -> bool:
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
            delay = self.COMMAND_INTERVAL_S if post_write_delay is None else post_write_delay
            if delay > 0:
                time.sleep(delay)
            return True
        except Exception as e:
            self._log(f"Error sending command: {e}")
            return False
    
    def _read_response(self, timeout: float = 2.0, max_len: int = 256, expected_len: Optional[int] = None) -> bytes:
        """
        Read response from camera with continuous polling.
        Matches the behavior of rs485_vc0706_camera._read_until_timeout()
        
        Args:
            timeout: Total timeout in seconds
            max_len: Maximum bytes to read
            expected_len: If set, stop immediately once this many bytes are received
            
        Returns:
            Response bytes
        """
        data = b''
        start_time = time.time()
        poll_interval = 0.05
        no_data_count = 0

        if expected_len is not None:
            expected_len = min(expected_len, max_len)
        
        self._log(f"Polling for response (timeout={timeout}s, max_len={max_len})...")
        
        while len(data) < max_len and (time.time() - start_time) < timeout:
            chunk = self.session.read()
            if chunk:
                data += chunk
                no_data_count = 0
                self._log(f"  Got {len(chunk)} bytes, total: {len(data)} bytes")
                if expected_len is not None and len(data) >= expected_len:
                    self._log(f"  Reached expected response length ({expected_len} bytes), stopping")
                    break
            else:
                no_data_count += 1
                # If we have some data and haven't received anything for a while, we might be done
                if data and no_data_count >= 30:  # 30 * 50ms = 1.5s of no data
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

    def _find_ack_offset(self, response: bytes, cmd: int) -> int:
        """Find VC0706 ACK frame offset for a command inside a noisy response."""
        if len(response) < 4:
            return -1

        for idx in range(0, len(response) - 3):
            if response[idx] != 0x76:
                continue
            if response[idx + 2] != cmd:
                continue
            # status byte 0x00 is normal ACK; some variants may emit 0x01 payload indicator
            if response[idx + 3] in (0x00, 0x01):
                return idx

        return -1

    def _poll_for_ack(
        self,
        *,
        cmd: int,
        label: str,
        timeout: float = 2.5,
        max_len: int = 256,
        max_attempts: int | None = None,
        inter_attempt_delay: float | None = None,
    ) -> tuple[int, bytes]:
        attempts = max_attempts or self.CONTROL_MAX_POLLS
        delay = self.COMMAND_INTERVAL_S if inter_attempt_delay is None else inter_attempt_delay
        last_response = b""
        last_nonempty_response = b""

        for attempt in range(1, attempts + 1):
            response = self._read_response(timeout=timeout, max_len=max_len)
            last_response = response
            if response:
                last_nonempty_response = response
            ack_offset = self._find_ack_offset(response, cmd)
            if ack_offset >= 0:
                if ack_offset > 0:
                    self._log(f"{label} ACK found at offset {ack_offset}; ignored leading bytes")
                return ack_offset, response

            if response:
                self._log(f"{label} poll {attempt}/{attempts} without ACK, response: {response.hex()}")
            else:
                self._log(f"{label} poll {attempt}/{attempts}: no response")

            if attempt < attempts and delay > 0:
                time.sleep(delay)

        return -1, (last_nonempty_response or last_response)
    
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
        ack_offset, response = self._poll_for_ack(
            cmd=self.CMD_GET_VERSION,
            label="GET_VERSION",
            timeout=2.5,
            max_len=256,
        )

        if ack_offset >= 0 and len(response) >= (ack_offset + 6):
            # Version string follows the header
            version = response[ack_offset + 5:].decode('ascii', errors='ignore').strip('\x00\r\n')
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

    def stop_frame(self) -> bool:
        self._log("Stopping frame...")
        if not self._send_command(self.CMD_FBUF_CTRL, bytes([self.FBUF_STOP_FRAME]), label="STOP_FRAME"):
            return False

        ack_offset, _ = self._poll_for_ack(
            cmd=self.CMD_FBUF_CTRL,
            label="STOP_FRAME",
            timeout=2.5,
            max_len=128,
        )
        if ack_offset >= 0:
            self._log("Frame stopped")
            return True

        return False

    def resume_frame(self) -> bool:
        self._log("Resuming frame capture...")
        if not self._send_command(self.CMD_FBUF_CTRL, bytes([self.FBUF_RESUME_FRAME]), label="RESUME_FRAME"):
            return False

        ack_offset, _ = self._poll_for_ack(
            cmd=self.CMD_FBUF_CTRL,
            label="RESUME_FRAME",
            timeout=2.0,
            max_len=128,
        )
        if ack_offset >= 0:
            self._log("Frame resumed")
            return True

        return False

    def get_frame_buffer_length(self) -> int:
        self._log("Getting frame buffer length...")

        if not self._send_command(self.CMD_GET_FBUF_LEN, bytes([0x00]), label="GET_FBUF_LEN"):
            return 0

        ack_offset, response = self._poll_for_ack(
            cmd=self.CMD_GET_FBUF_LEN,
            label="GET_FBUF_LEN",
            timeout=2.5,
            max_len=128,
        )
        if ack_offset >= 0 and len(response) >= (ack_offset + 9):
            length = (
                (response[ack_offset + 5] << 24)
                | (response[ack_offset + 6] << 16)
                | (response[ack_offset + 7] << 8)
                | response[ack_offset + 8]
            )
            self._log(f"Frame buffer length: {length} bytes")
            return length

        return 0

    def read_frame_buffer(self, length: int, max_retries: int = 3) -> Optional[bytes]:
        if length <= 0 or length > self.MAX_FRAME_SIZE:
            self._log(f"Invalid frame length: {length}")
            return None

        image = bytearray()
        offset = 0

        while offset < length:
            chunk_size = min(self.READ_CHUNK_SIZE, length - offset)
            args = bytes([
                0x00, 0x0A,
                (offset >> 24) & 0xFF,
                (offset >> 16) & 0xFF,
                (offset >> 8) & 0xFF,
                offset & 0xFF,
                (chunk_size >> 24) & 0xFF,
                (chunk_size >> 16) & 0xFF,
                (chunk_size >> 8) & 0xFF,
                chunk_size & 0xFF,
                0x00,
                0xFF,
            ])

            response = b""
            for attempt in range(1, max_retries + 1):
                if not self._send_command(
                    self.CMD_READ_FBUF,
                    args,
                    label=f"READ_FBUF@{offset}#{attempt}",
                    post_write_delay=0.0,
                ):
                    continue
                response = self._read_response(
                    timeout=4.0,
                    max_len=chunk_size + 10,
                    expected_len=chunk_size + 10,
                )
                ack_offset = self._find_ack_offset(response, self.CMD_READ_FBUF)
                if ack_offset >= 0 and len(response) >= (ack_offset + 5 + chunk_size):
                    break
                self._log(
                    f"Retry chunk offset {offset} attempt {attempt}/{max_retries}, got {len(response)} bytes"
                )

            if len(response) < 10:
                self._log(f"Short response at offset {offset}: {len(response)} bytes")
                return None

            ack_offset = self._find_ack_offset(response, self.CMD_READ_FBUF)
            if ack_offset < 0:
                self._log(f"Invalid read-fbuf header at offset {offset}: {response[:8].hex()}")
                return None

            payload_start = ack_offset + 5
            payload_end = payload_start + chunk_size
            if len(response) < payload_end:
                self._log(
                    f"Chunk too short at offset {offset}: got {len(response) - payload_start}, need {chunk_size}"
                )
                return None

            image.extend(response[payload_start:payload_end])
            offset += chunk_size

            progress = (offset * 100) // length
            print(f"\rRead progress: {progress}% ({offset}/{length})", end="", flush=True)

        print()
        return bytes(image)

    def capture_image(self, max_retries: int = 3) -> Optional[bytes]:
        if not self.stop_frame():
            self._log("Failed to stop frame")
            return None

        frame_len = 0
        for _ in range(max_retries):
            self.drain_buffer(0.1)
            frame_len = self.get_frame_buffer_length()
            if frame_len > 0:
                break

        if frame_len <= 0:
            self._log("Failed to get frame length")
            self.resume_frame()
            return None

        data = self.read_frame_buffer(frame_len)
        self.resume_frame()
        return data

    def save_image(self, data: bytes, filename: str) -> bool:
        try:
            with open(filename, "wb") as fp:
                fp.write(data)
            print(f"Saved image: {filename} ({len(data)} bytes)")
            return True
        except OSError as exc:
            print(f"Failed to save image {filename}: {exc}")
            return False
    
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
        
        # Read acknowledgment
        ack_offset, response = self._poll_for_ack(
            cmd=self.CMD_RESET,
            label="RESET",
            timeout=3.0,
            max_len=128,
        )

        if ack_offset >= 0:
            self._log(f"Reset response: {response[:min(16, len(response))].hex()}")
            return True
        
        self._log("Reset: No response")
        return False

    def set_resolution(self) -> bool:
        """
        Set camera resolution using VC0706 downsize command.

        Sends exact payload requested:
        56 00 31 05 04 01 00 19 00

        Returns:
            True if ACK is received
        """
        self._log("Setting camera resolution (56 00 31 05 04 01 00 19 00)...")

        args = bytes([0x04, 0x01, 0x00, 0x19, 0x00])
        self.drain_buffer(0.2)
        if not self._send_command(self.CMD_SET_DOWNSIZE, args, label="SET_RESOLUTION"):
            return False

        ack_offset, _ = self._poll_for_ack(
            cmd=self.CMD_SET_DOWNSIZE,
            label="SET_RESOLUTION",
            timeout=2.5,
            max_len=256,
        )
        if ack_offset >= 0:
            self._log("Resolution command acknowledged")
            return True

        return False


def main():
    parser = argparse.ArgumentParser(
        description="Test VC0706 camera via UART REST API"
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
        help="UART object instance (default: 0)"
    )
    parser.add_argument(
        "--baud", "-b",
        type=int,
        default=921600,
        help="Baud rate (default: 921600)"
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
        "--action",
        choices=["version", "capture", "set-resolution"],
        default="capture",
        help="Action to run (default: capture)"
    )
    parser.add_argument(
        "--output", "-o",
        default="capture.jpg",
        help="Output file for --action capture (default: capture.jpg)"
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Reset camera before action"
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
    parser.add_argument(
        "--power-on-wait",
        type=float,
        default=2.0,
        help="Delay in seconds after enabling interface power (default: 2.0)"
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
    camera_kwargs = dict(
        client=client,
        endpoint=args.client,
        instance=args.instance,
        baudrate=args.baud,
        tx_pin=args.tx_pin,
        rx_pin=args.rx_pin,
        debug=not args.quiet,
    )
    if args.power_on_wait is not None:
        camera_kwargs["power_on_wait"] = args.power_on_wait
    camera = VC0706RestCamera(**camera_kwargs)
    
    try:
        # Start log polling
        if log_poller:
            print("Starting device log polling...")
            log_poller.start()
        
        # Connect
        if not camera.connect():
            print("Failed to connect to camera")
            return 1
        
        # Reset if requested
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
        
        # Fetch any remaining logs
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
        else:
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
