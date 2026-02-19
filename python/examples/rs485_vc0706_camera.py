#!/usr/bin/env python3
"""
VC0706 Camera Control Script

This script implements the VC0706 serial camera protocol for use on a computer.
It allows listing available serial ports, connecting to a VC0706 camera,
and capturing JPEG images.

Usage:
    python vc0706_camera.py [options]

Options:
    --list          List available serial ports
    --port PORT     Serial port to use (e.g., /dev/ttyUSB0 or COM3)
    --baud BAUD     Baud rate (default: 38400)
    --output FILE   Output filename for captured image (default: capture.jpg)
    --resolution    Set resolution: 640x480, 320x240, or 160x120
    --continuous    Continuously capture images
    --interval SEC  Interval between captures in continuous mode (default: 2)
"""

import argparse
import sys
import time
import os
from typing import Optional, List, Tuple

try:
    import serial
    import serial.tools.list_ports
except ImportError:
    print("Error: pyserial is required. Install with: pip install pyserial")
    sys.exit(1)


class VC0706Camera:
    """VC0706 Serial Camera Controller"""
    
    # Protocol constants
    SERIAL_NUM = 0x00  # Camera serial number (usually 0x00)
    
    # Command codes
    CMD_GET_VERSION = 0x11
    CMD_SET_SERIAL = 0x21
    CMD_RESET = 0x26
    CMD_READ_FBUF = 0x32
    CMD_GET_FBUF_LEN = 0x34
    CMD_FBUF_CTRL = 0x36
    CMD_DOWNSIZE_SIZE = 0x53
    CMD_DOWNSIZE_STATUS = 0x54
    CMD_COMPRESSION = 0x31
    CMD_MOTION_CTRL = 0x37
    CMD_MOTION_STATUS = 0x38
    CMD_MOTION_DETECT = 0x39
    CMD_WRITE_DATA = 0x31
    CMD_READ_DATA = 0x30
    
    # Frame buffer control types
    FBUF_STOP_FRAME = 0x00
    FBUF_STEP_FRAME = 0x01
    FBUF_RESUME_FRAME = 0x02
    
    # Image sizes
    SIZE_640x480 = 0x00
    SIZE_320x240 = 0x11
    SIZE_160x120 = 0x22
    
    # Resolutions (for set_image_size command via write data)
    RES_VGA = 0x44  # 640x480
    RES_QVGA = 0x55  # 320x240
    RES_QQVGA = 0x22  # 160x120
    
    # Read chunk size
    READ_CHUNK_SIZE = 4096  # Larger chunks for faster transfer
    MAX_FRAME_SIZE = 500000  # 500KB for 2MP camera (1920x1080)
    
    def __init__(self, port: str, baud: int = 38400, timeout: float = 1.0, debug: bool = False):
        """
        Initialize VC0706 camera connection.
        
        Args:
            port: Serial port name (e.g., '/dev/ttyUSB0' or 'COM3')
            baud: Baud rate (default: 38400)
            timeout: Serial timeout in seconds
        """
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.serial: Optional[serial.Serial] = None
        self.debug = debug
        self.frame_ptr = 0
        self.buffer_len = 0
        
    def connect(self) -> bool:
        """
        Connect to the camera.
        
        Returns:
            True if connection successful, False otherwise
        """
        try:
            self.serial = serial.Serial(
                port=self.port,
                baudrate=self.baud,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_NONE,
                stopbits=serial.STOPBITS_ONE,
                timeout=self.timeout
            )
            # Wait for camera to be ready
            time.sleep(0.3)
            return True
        except serial.SerialException as e:
            print(f"Error connecting to {self.port}: {e}")
            return False
    
    def disconnect(self):
        """Disconnect from the camera."""
        if self.serial and self.serial.is_open:
            self.serial.close()
            self.serial = None
    
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
        if not self.serial or not self.serial.is_open:
            print("Error: Serial port not open")
            return False
        
        packet = self._build_command(cmd, args)
        
        if label:
            print(f"TX {label}: {packet.hex()}")
        elif self.debug:
            print(f"TX: {packet.hex()}")
        
        try:
            self.serial.write(packet)
            self.serial.flush()
            return True
        except serial.SerialException as e:
            print(f"Error sending command: {e}")
            return False
    
    def _read_response(self, expected_len: int, timeout: float = None, label: str = "") -> bytes:
        """
        Read response from camera.
        
        Args:
            expected_len: Expected response length
            timeout: Read timeout (uses default if None)
            
        Returns:
            Response bytes
        """
        if not self.serial:
            return b''
        
        old_timeout = self.serial.timeout
        if timeout is not None:
            self.serial.timeout = timeout
        
        try:
            data = self.serial.read(expected_len)
            if self.debug and data:
                if label:
                    print(f"RX {label}: {data.hex()}")
                else:
                    print(f"RX: {data.hex()}")
            return data
        finally:
            self.serial.timeout = old_timeout
    
    def _read_until_timeout(self, max_len: int = 256, timeout: float = 0.5, label: str = "") -> bytes:
        """
        Read data until timeout or max_len reached.
        
        Args:
            max_len: Maximum bytes to read
            timeout: Total timeout
            
        Returns:
            Response bytes
        """
        if not self.serial:
            return b''
        
        data = b''
        start_time = time.time()
        
        while len(data) < max_len and (time.time() - start_time) < timeout:
            if self.serial.in_waiting > 0:
                chunk = self.serial.read(min(self.serial.in_waiting, max_len - len(data)))
                data += chunk
            else:
                time.sleep(0.01)
        
        if self.debug and data:
            if label:
                print(f"RX {label} (hex): {data.hex()}")
                print(f"RX {label} (ascii): {data.decode('ascii', errors='replace')}")
            else:
                print(f"RX (hex): {data.hex()}")
                print(f"RX (ascii): {data.decode('ascii', errors='replace')}")
        return data
    
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
    
    def drain_buffer(self, timeout: float = 0.1) -> int:
        """
        Drain any stray data in the receive buffer.
        
        Args:
            timeout: How long to wait for data
            
        Returns:
            Number of bytes drained
        """
        if not self.serial:
            return 0
        
        drained = 0
        start = time.time()
        while (time.time() - start) < timeout:
            if self.serial.in_waiting > 0:
                data = self.serial.read(self.serial.in_waiting)
                drained += len(data)
            else:
                time.sleep(0.01)
        
        if drained > 0:
            print(f"Drained {drained} stray bytes")
        
        return drained
    
    def reset(self) -> bool:
        """
        Reset the camera.
        
        Returns:
            True if reset successful
        """
        print("Resetting camera...")
        
        # Command: 56 00 26 00
        if not self._send_command(self.CMD_RESET, label="RESET"):
            return False
        
        # Read acknowledgment
        response = self._read_until_timeout(64, timeout=2.0)
        
        if len(response) >= 4:
            print(f"RX reset ack: {response[:min(16, len(response))].hex()}")
            # After reset, camera sends "Init end\r\n" string
            time.sleep(2.0)  # Give camera time to reboot
            return True
        
        print("Reset: No response")
        return False
    
    def get_version(self) -> Optional[str]:
        """
        Get camera firmware version.
        
        Returns:
            Version string or None if failed
        """
        print("Getting camera version...")
        
        # Command: 56 00 11 00
        if not self._send_command(self.CMD_GET_VERSION, label="GET_VERSION"):
            return None
        
        # Response: 76 00 11 00 [version string]
        # PTC2M0 cameras return longer ASCII responses, so read more
        response = self._read_until_timeout(256, timeout=2.0)
        
        if len(response) >= 5 and self._verify_response(response, self.CMD_GET_VERSION):
            # Version string follows the header
            version = response[5:].decode('ascii', errors='ignore').strip('\x00\r\n')
            print(f"Camera version: {version}")
            return version
        
        # Try to parse as plain ASCII (for PTC2M0 and similar cameras)
        try:
            ascii_resp = response.decode('ascii', errors='ignore')
            if 'Version' in ascii_resp or 'PTC' in ascii_resp:
                print(f"Camera info (ASCII):\n{ascii_resp}")
                return ascii_resp.strip()
        except:
            pass
        
        print(f"RX version (hex): {response.hex()}")
        print(f"RX version (ascii): {response.decode('ascii', errors='replace')}")
        return None
    
    def set_image_size(self, size: int) -> bool:
        """
        Set image resolution.
        
        Args:
            size: Resolution code (RES_VGA, RES_QVGA, or RES_QQVGA)
            
        Returns:
            True if successful
        """
        size_names = {
            self.RES_VGA: "640x480 (VGA)",
            self.RES_QVGA: "320x240 (QVGA)", 
            self.RES_QQVGA: "160x120 (QQVGA)"
        }
        print(f"Setting resolution to {size_names.get(size, 'unknown')}...")
        
        # Command: 56 00 54 01 [size]
        if not self._send_command(self.CMD_DOWNSIZE_STATUS, bytes([size]), label="SET_SIZE"):
            return False
        
        # Response: 76 00 54 00 00
        response = self._read_response(5, timeout=1.0)
        
        if len(response) >= 5 and self._verify_response(response, self.CMD_DOWNSIZE_STATUS):
            print("Resolution set successfully")
            return True
        
        print(f"RX set size (raw): {response.hex()}")
        return False
    
    def stop_frame(self) -> bool:
        """
        Stop/freeze the current frame.
        
        Returns:
            True if successful
        """
        print("Stopping frame...")
        
        # Command: 56 00 36 01 00
        if not self._send_command(self.CMD_FBUF_CTRL, bytes([self.FBUF_STOP_FRAME]), label="STOP_FRAME"):
            return False
        
        # Response: 76 00 36 00 00
        response = self._read_until_timeout(64, timeout=1.5)
        
        if len(response) >= 5 and self._verify_response(response, self.CMD_FBUF_CTRL):
            print("Frame stopped")
            return True
        
        # Check for ASCII response (PTC2M0 camera)
        ascii_resp = response.decode('ascii', errors='ignore')
        if ascii_resp:
            print(f"RX stop frame (ascii): {ascii_resp}")
            # Consider it successful if we got a response
            return len(response) > 0
        
        print(f"RX stop frame (hex): {response.hex()}")
        return False
    
    def resume_frame(self) -> bool:
        """
        Resume frame capture.
        
        Returns:
            True if successful
        """
        print("Resuming frame capture...")
        
        # Command: 56 00 36 01 02
        if not self._send_command(self.CMD_FBUF_CTRL, bytes([self.FBUF_RESUME_FRAME]), label="RESUME_FRAME"):
            return False
        
        # Response: 76 00 36 00 00 (or ASCII "error" on some cameras)
        response = self._read_until_timeout(64, timeout=1.0)
        
        if len(response) >= 5 and self._verify_response(response, self.CMD_FBUF_CTRL):
            print("Frame capture resumed")
            return True
        
        # Check for ASCII response
        ascii_resp = response.decode('ascii', errors='ignore')
        if ascii_resp:
            print(f"RX resume frame (ascii): {ascii_resp}")
        else:
            print(f"RX resume frame (hex): {response.hex()}")
        
        # Consider it successful if we got any response
        return len(response) > 0
    
    def get_frame_buffer_length(self) -> int:
        """
        Get the length of the current frame buffer.
        
        Returns:
            Frame buffer length in bytes, or 0 if failed
        """
        # Command: 56 00 34 01 00
        if not self._send_command(self.CMD_GET_FBUF_LEN, bytes([0x00]), label="GET_FBUF_LEN"):
            return 0
        
        # Response: 76 00 34 00 04 LL LL LL LL
        response = self._read_until_timeout(64, timeout=2.0)
        
        if len(response) >= 9 and self._verify_response(response, self.CMD_GET_FBUF_LEN):
            length = (response[5] << 24) | (response[6] << 16) | (response[7] << 8) | response[8]
            print(f"Frame buffer length: {length} bytes")
            return length
        
        print(f"RX get fbuf len (hex): {response.hex()}")
        print(f"RX get fbuf len (ascii): {response.decode('ascii', errors='replace')}")
        return 0
    
    def read_frame_buffer(self, length: int) -> Optional[bytes]:
        """
        Read the frame buffer data.
        
        Args:
            length: Number of bytes to read
            
        Returns:
            Frame data bytes or None if failed
        """
        if length == 0 or length > self.MAX_FRAME_SIZE:
            print(f"Invalid frame length: {length}")
            return None
        
        print(f"Reading {length} bytes from frame buffer...")
        
        frame_data = bytearray()
        offset = 0
        chunk_count = 0
        
        while offset < length:
            chunk_size = min(self.READ_CHUNK_SIZE, length - offset)
            
            # Build read command: 56 00 32 0C 00 0A [addr:4] [len:4] 00 FF
            args = bytes([
                0x00, 0x0A,
                (offset >> 24) & 0xFF, (offset >> 16) & 0xFF, 
                (offset >> 8) & 0xFF, offset & 0xFF,
                (chunk_size >> 24) & 0xFF, (chunk_size >> 16) & 0xFF,
                (chunk_size >> 8) & 0xFF, chunk_size & 0xFF,
                0x00, 0xFF  # Delay
            ])
            
            label = f"READ_FBUF chunk {chunk_count}" if chunk_count < 2 else ""
            if not self._send_command(self.CMD_READ_FBUF, args, label=label):
                return None
            
            # Read ACK header: 76 00 32 00 00
            ack = self._read_response(5, timeout=1.0)
            if len(ack) < 5 or not self._verify_response(ack, self.CMD_READ_FBUF):
                print(f"Bad ACK at offset {offset}: {ack.hex()}")
                return None
            
            # Read chunk data
            chunk_data = self._read_response(chunk_size, timeout=2.0)
            if len(chunk_data) != chunk_size:
                print(f"Short read at offset {offset}: got {len(chunk_data)}/{chunk_size}")
                return None
            
            # Read tail: 76 00 32 00 00
            tail = self._read_response(5, timeout=0.5)
            if len(tail) < 5:
                print(f"Missing tail at offset {offset}")
            
            frame_data.extend(chunk_data)
            offset += chunk_size
            chunk_count += 1
            
            # Progress indicator
            progress = (offset * 100) // length
            print(f"\rProgress: {progress}% ({offset}/{length} bytes)", end='', flush=True)
        
        print()  # Newline after progress
        
        # Trim VC0706 frame wrappers if present
        vc0706_hdr = bytes([0x76, 0x00, 0x32, 0x00, 0x00])
        if frame_data[:5] == vc0706_hdr:
            frame_data = frame_data[5:]
            print("Trimmed VC0706 prefix")
        if frame_data[-5:] == vc0706_hdr:
            frame_data = frame_data[:-5]
            print("Trimmed VC0706 suffix")
        
        return bytes(frame_data)
    
    def take_photo(self) -> bool:
        """
        Take a photo (same as stop_frame).
        
        Returns:
            True if successful
        """
        return self.stop_frame()
    
    def capture_image(self, max_retries: int = 3) -> Optional[bytes]:
        """
        Capture an image from the camera.
        
        Args:
            max_retries: Maximum number of retries for getting frame length
            
        Returns:
            JPEG image data or None if failed
        """
        # Stop frame to capture current image
        if not self.take_photo():
            print("Failed to capture frame")
            return None
        
        time.sleep(0.4)
        
        # Get frame buffer length with retries
        frame_len = 0
        for attempt in range(1, max_retries + 1):
            self.drain_buffer(0.1)
            frame_len = self.get_frame_buffer_length()
            if frame_len > 0:
                break
            print(f"Retry {attempt}/{max_retries} - frame length was 0")
            time.sleep(0.5)
        
        if frame_len == 0:
            print("Failed to get frame length")
            self.resume_frame()
            return None
        
        # Read the frame buffer
        image_data = self.read_frame_buffer(frame_len)
        
        # Resume frame capture
        self.resume_frame()
        
        return image_data
    
    def save_image(self, data: bytes, filename: str) -> bool:
        """
        Save image data to file.
        
        Args:
            data: Image data bytes
            filename: Output filename
            
        Returns:
            True if saved successfully
        """
        try:
            with open(filename, 'wb') as f:
                f.write(data)
            print(f"Image saved to {filename} ({len(data)} bytes)")
            return True
        except IOError as e:
            print(f"Error saving image: {e}")
            return False


def list_serial_ports() -> List[Tuple[str, str, str]]:
    """
    List available serial ports.
    
    Returns:
        List of (port, description, hardware_id) tuples
    """
    ports = serial.tools.list_ports.comports()
    return [(p.device, p.description, p.hwid) for p in ports]


def print_serial_ports():
    """Print available serial ports to console."""
    ports = list_serial_ports()
    
    if not ports:
        print("No serial ports found.")
        return
    
    print("\nAvailable serial ports:")
    print("-" * 70)
    for i, (device, description, hwid) in enumerate(ports, 1):
        print(f"  {i}. {device}")
        print(f"     Description: {description}")
        print(f"     Hardware ID: {hwid}")
        print()


def select_serial_port() -> Optional[str]:
    """
    Interactive serial port selection.
    
    Returns:
        Selected port name or None if cancelled
    """
    ports = list_serial_ports()
    
    if not ports:
        print("No serial ports found.")
        return None
    
    print("\nAvailable serial ports:")
    print("-" * 50)
    for i, (device, description, _) in enumerate(ports, 1):
        print(f"  {i}. {device} - {description}")
    print()
    
    while True:
        try:
            choice = input("Select port number (or 'q' to quit): ").strip()
            
            if choice.lower() == 'q':
                return None
            
            idx = int(choice) - 1
            if 0 <= idx < len(ports):
                return ports[idx][0]
            else:
                print(f"Please enter a number between 1 and {len(ports)}")
        except ValueError:
            print("Invalid input. Please enter a number.")
        except KeyboardInterrupt:
            print()
            return None


def parse_resolution(res_str: str) -> Optional[int]:
    """Parse resolution string to VC0706 resolution code."""
    res_map = {
        '640x480': VC0706Camera.RES_VGA,
        'vga': VC0706Camera.RES_VGA,
        '320x240': VC0706Camera.RES_QVGA,
        'qvga': VC0706Camera.RES_QVGA,
        '160x120': VC0706Camera.RES_QQVGA,
        'qqvga': VC0706Camera.RES_QQVGA,
    }
    return res_map.get(res_str.lower())


def main():
    parser = argparse.ArgumentParser(
        description='VC0706 Camera Control',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --list                     List available serial ports
  %(prog)s --port /dev/ttyUSB0        Capture image using specified port
  %(prog)s                            Interactive mode (select port)
  %(prog)s --port COM3 --output photo.jpg
  %(prog)s --port /dev/ttyUSB0 --resolution 320x240
  %(prog)s --port /dev/ttyUSB0 --continuous --interval 5
        """
    )
    
    parser.add_argument('--list', action='store_true',
                        help='List available serial ports')
    parser.add_argument('--port', '-p', type=str,
                        help='Serial port to use')
    parser.add_argument('--baud', '-b', type=int, default=38400,
                        help='Baud rate (default: 38400)')
    parser.add_argument('--output', '-o', type=str, default='capture.jpg',
                        help='Output filename (default: capture.jpg)')
    parser.add_argument('--resolution', '-r', type=str,
                        choices=['640x480', 'vga', '320x240', 'qvga', '160x120', 'qqvga'],
                        help='Image resolution')
    parser.add_argument('--continuous', '-c', action='store_true',
                        help='Continuous capture mode')
    parser.add_argument('--interval', '-i', type=float, default=2.0,
                        help='Interval between captures in continuous mode (default: 2 sec)')
    parser.add_argument('--no-reset', action='store_true',
                        help='Skip camera reset on startup')
    parser.add_argument('--debug', '-d', action='store_true',
                        help='Enable debug output')
    parser.add_argument('--explore', '-e', action='store_true',
                        help='Exploration mode - send commands interactively')
    
    args = parser.parse_args()
    
    # List ports and exit
    if args.list:
        print_serial_ports()
        return 0
    
    # Get port - either from argument or interactive selection
    port = args.port
    if not port:
        print("VC0706 Camera Control")
        print("=" * 50)
        port = select_serial_port()
        if not port:
            print("No port selected. Exiting.")
            return 1
    
    print(f"\nConnecting to {port} at {args.baud} baud...")
    
    # Create camera instance
    camera = VC0706Camera(port, args.baud, debug=args.debug)
    
    try:
        # Connect
        if not camera.connect():
            return 1
        
        print("Connected!")
        
        # Exploration mode for figuring out protocol
        if args.explore:
            print("\n=== Exploration Mode ===")
            print("Commands:")
            print("  hex XX XX XX ...  - Send hex bytes")
            print("  text <string>     - Send ASCII text + CR LF")
            print("  read              - Read and display response")
            print("  reset             - Send VC0706 reset")
            print("  version           - Send VC0706 version request")
            print("  quit              - Exit")
            print()
            
            while True:
                try:
                    cmd = input("> ").strip()
                    if not cmd:
                        continue
                    
                    if cmd.lower() == 'quit':
                        break
                    elif cmd.lower() == 'read':
                        resp = camera._read_until_timeout(512, timeout=1.0)
                        if resp:
                            print(f"RX (hex): {resp.hex()}")
                            print(f"RX (ascii): {resp.decode('ascii', errors='replace')}")
                        else:
                            print("No data received")
                    elif cmd.lower() == 'drain':
                        camera.drain_buffer(0.5)
                        resp = camera._read_until_timeout(1024, timeout=1.0)
                        if resp:
                            print(f"Drained (hex): {resp.hex()}")
                            print(f"Drained (ascii): {resp.decode('ascii', errors='replace')}")
                        else:
                            print("Buffer empty")
                    elif cmd.lower() == 'reset':
                        camera._send_command(camera.CMD_RESET, label="RESET")
                        time.sleep(2.0)  # Wait for camera to reboot
                        resp = camera._read_until_timeout(512, timeout=2.0)
                        print(f"RX (hex): {resp.hex()}")
                        print(f"RX (ascii): {resp.decode('ascii', errors='replace')}")
                    elif cmd.lower() == 'version':
                        camera._send_command(camera.CMD_GET_VERSION, label="GET_VERSION")
                        time.sleep(0.5)
                        resp = camera._read_until_timeout(512, timeout=2.0)
                        print(f"RX (hex): {resp.hex()}")
                        print(f"RX (ascii): {resp.decode('ascii', errors='replace')}")
                    elif cmd.lower() == 'stop':
                        camera._send_command(camera.CMD_FBUF_CTRL, bytes([camera.FBUF_STOP_FRAME]), label="STOP_FRAME")
                        time.sleep(0.5)
                        resp = camera._read_until_timeout(512, timeout=2.0)
                        print(f"RX (hex): {resp.hex()}")
                        print(f"RX (ascii): {resp.decode('ascii', errors='replace')}")
                    elif cmd.lower() == 'resume':
                        camera._send_command(camera.CMD_FBUF_CTRL, bytes([camera.FBUF_RESUME_FRAME]), label="RESUME_FRAME")
                        time.sleep(0.3)
                        resp = camera._read_until_timeout(512, timeout=1.0)
                        print(f"RX (hex): {resp.hex()}")
                        print(f"RX (ascii): {resp.decode('ascii', errors='replace')}")
                    elif cmd.lower() == 'len':
                        camera._send_command(camera.CMD_GET_FBUF_LEN, bytes([0x00]), label="GET_FBUF_LEN")
                        time.sleep(0.5)
                        resp = camera._read_until_timeout(512, timeout=2.0)
                        print(f"RX (hex): {resp.hex()}")
                        print(f"RX (ascii): {resp.decode('ascii', errors='replace')}")
                        # Try to parse length from response
                        if len(resp) >= 9 and resp[0] == 0x76:
                            length = (resp[5] << 24) | (resp[6] << 16) | (resp[7] << 8) | resp[8]
                            print(f"Parsed length: {length} bytes")
                    elif cmd.lower().startswith('readimg '):
                        # Read image data: readimg <length>
                        try:
                            length = int(cmd.split()[1])
                            print(f"Reading {length} bytes...")
                            camera._send_command(camera.CMD_READ_FBUF, bytes([
                                0x00, 0x0A,
                                0x00, 0x00, 0x00, 0x00,  # offset = 0
                                (length >> 24) & 0xFF, (length >> 16) & 0xFF,
                                (length >> 8) & 0xFF, length & 0xFF,
                                0x00, 0xFF  # delay
                            ]), label="READ_FBUF")
                            time.sleep(0.5)
                            resp = camera._read_until_timeout(length + 20, timeout=5.0)
                            print(f"RX len: {len(resp)}")
                            print(f"RX first 64 (hex): {resp[:64].hex()}")
                            print(f"RX first 64 (ascii): {resp[:64].decode('ascii', errors='replace')}")
                            if len(resp) > 64:
                                print(f"RX last 32 (hex): {resp[-32:].hex()}")
                        except (ValueError, IndexError):
                            print("Usage: readimg <length>")
                    elif cmd.lower().startswith('hex '):
                        hex_str = cmd[4:].replace(' ', '')
                        try:
                            data = bytes.fromhex(hex_str)
                            camera.serial.write(data)
                            camera.serial.flush()
                            print(f"TX (hex): {data.hex()}")
                            time.sleep(0.5)
                            resp = camera._read_until_timeout(512, timeout=2.0)
                            if resp:
                                print(f"RX (hex): {resp.hex()}")
                                print(f"RX (ascii): {resp.decode('ascii', errors='replace')}")
                        except ValueError as e:
                            print(f"Invalid hex: {e}")
                    elif cmd.lower().startswith('text '):
                        text = cmd[5:] + '\r\n'
                        camera.serial.write(text.encode('ascii'))
                        camera.serial.flush()
                        print(f"TX (text): {repr(text)}")
                        time.sleep(0.5)
                        resp = camera._read_until_timeout(512, timeout=2.0)
                        if resp:
                            print(f"RX (hex): {resp.hex()}")
                            print(f"RX (ascii): {resp.decode('ascii', errors='replace')}")
                    elif cmd.lower() == 'help':
                        print("Commands:")
                        print("  hex XX XX XX ...  - Send hex bytes")
                        print("  text <string>     - Send ASCII text + CR LF")
                        print("  read              - Read and display response")
                        print("  drain             - Drain all pending data")
                        print("  reset             - Send VC0706 reset")
                        print("  version           - Send VC0706 version request")
                        print("  stop              - Stop/freeze frame")
                        print("  resume            - Resume frame capture")
                        print("  len               - Get frame buffer length")
                        print("  readimg <len>     - Read image data")
                        print("  quit              - Exit")
                    else:
                        print("Unknown command. Type 'help' for list of commands")
                except EOFError:
                    break
            return 0
        
        # Reset camera (unless skipped)
        if not args.no_reset:
            if not camera.reset():
                print("Warning: Reset failed, continuing anyway...")
        
        # Get version
        version = camera.get_version()
        if not version:
            print("Warning: Could not get camera version")
        
        # Set resolution if specified
        if args.resolution:
            res_code = parse_resolution(args.resolution)
            if res_code is not None:
                if not camera.set_image_size(res_code):
                    print("Warning: Could not set resolution")
                # Need to reset after changing resolution
                time.sleep(0.5)
                camera.reset()
        
        # Capture mode
        if args.continuous:
            print(f"\nContinuous capture mode (interval: {args.interval}s)")
            print("Press Ctrl+C to stop\n")
            
            count = 0
            while True:
                count += 1
                # Generate filename with timestamp
                base, ext = os.path.splitext(args.output)
                filename = f"{base}_{count:04d}{ext}"
                
                print(f"\n--- Capture {count} ---")
                image_data = camera.capture_image()
                
                if image_data:
                    camera.save_image(image_data, filename)
                else:
                    print(f"Capture {count} failed")
                
                time.sleep(args.interval)
        else:
            # Single capture
            print("\nCapturing image...")
            image_data = camera.capture_image()
            
            if image_data:
                camera.save_image(image_data, args.output)
                return 0
            else:
                print("Failed to capture image")
                return 1
                
    except KeyboardInterrupt:
        print("\n\nInterrupted by user")
        return 0
    finally:
        camera.disconnect()
        print("Disconnected")


if __name__ == '__main__':
    sys.exit(main())
