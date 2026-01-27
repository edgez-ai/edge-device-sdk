from __future__ import annotations

import sys
import time
from typing import Optional

from core.uart_client import UartSession

VC0706_GEN_VERSION = 0x11
VC0706_RESET = 0x26
VC0706_FBUF_CTRL = 0x36
VC0706_GET_FBUF_LEN = 0x34
VC0706_READ_FBUF = 0x32
VC0706_SET_PORT = 0x24

VC0706_STOP_CURRENT_FRAME = 0x00
VC0706_RESUME_FRAME = 0x02  # Match C code: 0x02 for resume

CAMERA_DELAY_MS = 10

BAUD_ARGUMENTS = {
    9600: bytes([0x03, 0x01, 0xAE, 0xC8]),
    19200: bytes([0x03, 0x01, 0x56, 0xE4]),
    38400: bytes([0x03, 0x01, 0x2A, 0xF2]),
    57600: bytes([0x03, 0x01, 0x1C, 0x1C]),
    115200: bytes([0x03, 0x01, 0x0D, 0xA6]),
}


def _hex(data: bytes) -> str:
    return data.hex() if data else "(empty)"


def _log(msg: str) -> None:
    print(f"[VC0706] {msg}", file=sys.stderr, flush=True)


class VC0706Camera:
    def __init__(
        self,
        session: UartSession,
        *,
        serial_num: int = 0,
        timeout_s: float = 0.5,
        poll_delay_s: float = 0.02,
        debug: bool = True,
    ) -> None:
        self.session = session
        self.serial_num = serial_num & 0xFF
        self.timeout_s = timeout_s
        self.poll_delay_s = poll_delay_s
        self._rx_buffer = bytearray()
        self._frame_ptr = 0
        self.debug = debug

    def _send_command(self, cmd: int, args: bytes = b"", label: str = "") -> None:
        payload = bytes([0x56, self.serial_num, cmd]) + args
        if self.debug:
            _log(f"TX {label or hex(cmd)}: {_hex(payload)}")
        self.session.write(payload)

    def _flush(self, max_reads: int = 3) -> None:
        """Flush any pending data from the receive buffer (limited reads)."""
        self._rx_buffer.clear()
        for _ in range(max_reads):
            chunk = self.session.read()
            if chunk:
                if self.debug:
                    _log(f"FLUSH: discarding {_hex(chunk)}")
            else:
                break
            time.sleep(0.02)

    def _read_exact(self, n: int, timeout_s: Optional[float] = None, label: str = "", max_retries: int = 3) -> bytes:
        """Read exactly n bytes with limited retries or a time-based timeout."""
        out = bytearray()
        if self._rx_buffer:
            take = min(n, len(self._rx_buffer))
            out += self._rx_buffer[:take]
            del self._rx_buffer[:take]

        if timeout_s is not None:
            deadline = time.monotonic() + max(0.0, timeout_s)
            while len(out) < n and time.monotonic() < deadline:
                chunk = self.session.read()
                if chunk:
                    if self.debug:
                        _log(f"RX chunk: {_hex(chunk)}")
                    self._rx_buffer.extend(chunk)
                    take = min(n - len(out), len(self._rx_buffer))
                    out += self._rx_buffer[:take]
                    del self._rx_buffer[:take]
                else:
                    time.sleep(self.poll_delay_s)
        else:
            retries = 0
            while len(out) < n and retries < max_retries:
                time.sleep(0.1)  # Wait for camera to respond
                chunk = self.session.read()
                if chunk:
                    if self.debug:
                        _log(f"RX chunk: {_hex(chunk)}")
                    self._rx_buffer.extend(chunk)
                    take = min(n - len(out), len(self._rx_buffer))
                    out += self._rx_buffer[:take]
                    del self._rx_buffer[:take]
                    retries = 0  # Reset on successful read
                else:
                    retries += 1

        if self.debug:
            _log(f"RX {label or 'exact'}({n}): {_hex(bytes(out))} (got {len(out)})")
        return bytes(out)

    def _read_collect(self, max_len: int, timeout_s: Optional[float] = None, label: str = "", max_retries: int = 3) -> bytes:
        """Read up to max_len bytes with limited retries or a time-based timeout."""
        out = bytearray()
        if self._rx_buffer:
            take = min(max_len, len(self._rx_buffer))
            out += self._rx_buffer[:take]
            del self._rx_buffer[:take]

        if timeout_s is not None:
            deadline = time.monotonic() + max(0.0, timeout_s)
            while len(out) < max_len and time.monotonic() < deadline:
                chunk = self.session.read()
                if chunk:
                    if self.debug:
                        _log(f"RX chunk: {_hex(chunk)}")
                    self._rx_buffer.extend(chunk)
                    take = min(max_len - len(out), len(self._rx_buffer))
                    out += self._rx_buffer[:take]
                    del self._rx_buffer[:take]
                else:
                    time.sleep(self.poll_delay_s)
        else:
            retries = 0
            while len(out) < max_len and retries < max_retries:
                time.sleep(0.1)  # Wait for camera to respond
                chunk = self.session.read()
                if chunk:
                    if self.debug:
                        _log(f"RX chunk: {_hex(chunk)}")
                    self._rx_buffer.extend(chunk)
                    take = min(max_len - len(out), len(self._rx_buffer))
                    out += self._rx_buffer[:take]
                    del self._rx_buffer[:take]
                    retries = 0  # Reset on successful read
                else:
                    retries += 1

        if self.debug:
            _log(f"RX {label or 'collect'}({max_len}): {_hex(bytes(out))} (got {len(out)})")
        return bytes(out)

    def _verify_response(self, resp: bytes, cmd: int, label: str = "") -> bool:
        """Verify response header: 76 00 <cmd> 00."""
        ok = len(resp) >= 4 and resp[0] == 0x76 and resp[1] == self.serial_num and resp[2] == cmd and resp[3] == 0x00
        if self.debug:
            _log(f"VERIFY {label or hex(cmd)}: {'OK' if ok else 'FAIL'} (resp={_hex(resp[:4] if len(resp)>=4 else resp)})")
        return ok

    def run_command(self, cmd: int, args: bytes, resplen: int, *, flush: bool = True, label: str = "") -> bool:
        if flush:
            self._flush()
        self._send_command(cmd, args, label=label)
        time.sleep(0.05)  # Small delay after sending command
        resp = self._read_exact(resplen, label=label)
        return self._verify_response(resp, cmd, label=label)

    def reset(self) -> bool:
        """Send reset command - camera responds with banner after ~2-3 seconds."""
        _log(">>> reset()")
        ok = self.run_command(VC0706_RESET, bytes([0x00]), 5, label="RESET")
        if ok:
            # After reset, camera sends a banner string. Wait and flush it.
            time.sleep(3.0)
            self._flush()
        return ok

    def get_version(self) -> str:
        """Get firmware version string."""
        _log(">>> get_version()")
        self._flush()
        self._send_command(VC0706_GEN_VERSION, bytes([0x00]), label="VERSION")  # C code uses 0x00 as arg
        time.sleep(0.1)
        resp = self._read_collect(64, timeout_s=1.0, label="VERSION")
        if not self._verify_response(resp, VC0706_GEN_VERSION, label="VERSION"):
            return resp.hex() if resp else "(no response)"
        payload = resp[5:]
        try:
            return payload.decode(errors="ignore").strip("\x00\r\n ")
        except Exception:
            return payload.hex()

    def set_baud(self, baudrate: int) -> bool:
        """Set camera's serial baudrate."""
        _log(f">>> set_baud({baudrate})")
        args = BAUD_ARGUMENTS.get(baudrate)
        if not args:
            raise ValueError(f"Unsupported baudrate {baudrate}")
        self._flush()
        self._send_command(VC0706_SET_PORT, args, label="SET_BAUD")
        time.sleep(0.1)
        resp = self._read_exact(5, timeout_s=0.5, label="SET_BAUD")
        return self._verify_response(resp, VC0706_SET_PORT, label="SET_BAUD")

    def take_picture(self) -> bool:
        """Freeze current frame (stop_current_frame = 0x00)."""
        _log(">>> take_picture()")
        self._frame_ptr = 0
        return self.run_command(VC0706_FBUF_CTRL, bytes([0x01, VC0706_STOP_CURRENT_FRAME]), 5, label="TAKE_PIC")

    def resume_video(self) -> bool:
        """Resume video streaming (resume_frame = 0x02 per C code)."""
        _log(">>> resume_video()")
        # Reset RX buffer before resume to ensure we can read the response
        self.session.reset_cursor()
        self._flush()
        return self.run_command(VC0706_FBUF_CTRL, bytes([0x01, VC0706_RESUME_FRAME]), 5, label="RESUME")

    def frame_length(self) -> int:
        """Get length of captured frame in bytes."""
        _log(">>> frame_length()")
        self._flush()
        self._send_command(VC0706_GET_FBUF_LEN, bytes([0x01, 0x00]), label="FRAME_LEN")
        time.sleep(0.1)
        resp = self._read_collect(16, timeout_s=1.0, label="FRAME_LEN")
        if not self._verify_response(resp, VC0706_GET_FBUF_LEN, label="FRAME_LEN") or len(resp) < 9:
            _log(f"frame_length() failed, resp={_hex(resp)}")
            return 0
        length = (resp[5] << 24) | (resp[6] << 16) | (resp[7] << 8) | resp[8]
        _log(f"frame_length() = {length}")
        return length

    def read_picture_chunk(self, size: int, *, retries: int = 0, retry_delay_s: float = 0.5) -> bytes:
        """Read a chunk of the captured image data."""
        if size <= 0:
            return b""
        size = min(size, 512)
        retries = max(0, retries)
        retry_delay_s = max(0.0, retry_delay_s)

        for attempt in range(retries + 1):
            _log(f">>> read_picture_chunk({size}) at ptr={self._frame_ptr}")

            # Reset RX buffer before each chunk to prevent buffer overflow
            self.session.reset_cursor()
            self._rx_buffer.clear()

            # Build read command matching C code format
            args = bytes(
                [
                    0x0C,  # FBUF type
                    0x00,  # Control mode
                    0x0A,  # Delay (10ms between packets)
                    (self._frame_ptr >> 24) & 0xFF,
                    (self._frame_ptr >> 16) & 0xFF,
                    (self._frame_ptr >> 8) & 0xFF,
                    self._frame_ptr & 0xFF,
                    (size >> 24) & 0xFF,
                    (size >> 16) & 0xFF,
                    (size >> 8) & 0xFF,
                    size & 0xFF,
                    0x00,  # Delay high
                    0x0A,  # Delay low (10 * 0.01ms = 0.1ms) - matching C code
                ]
            )
            self._send_command(VC0706_READ_FBUF, args, label=f"READ_CHUNK@{self._frame_ptr}")
            time.sleep(0.05)

            # Read ACK header (5 bytes)
            ack = self._read_exact(5, timeout_s=1.0, label="READ_ACK")
            if not self._verify_response(ack, VC0706_READ_FBUF, label="READ_ACK"):
                _log("read_picture_chunk: ACK failed")
                if attempt < retries:
                    time.sleep(retry_delay_s * (2**attempt))
                    continue
                return b""

            # Read actual image data
            data = self._read_exact(size, timeout_s=2.0, label="READ_DATA")
            if len(data) != size:
                _log(f"read_picture_chunk: got {len(data)} bytes, expected {size}")
                if attempt < retries:
                    time.sleep(retry_delay_s * (2**attempt))
                    continue
                return b""

            # Read trailing ACK (5 bytes)
            tail = self._read_exact(5, timeout_s=0.5, label="READ_TAIL")
            if not self._verify_response(tail, VC0706_READ_FBUF, label="READ_TAIL"):
                _log("read_picture_chunk: tail ACK failed")
                if attempt < retries:
                    time.sleep(retry_delay_s * (2**attempt))
                    continue
                return b""

            self._frame_ptr += len(data)
            _log(f"read_picture_chunk: OK, new ptr={self._frame_ptr}")
            return data

        return b""

    def read_picture(self, total_len: int, chunk_size: int = 64, *, chunk_retries: int = 0, retry_delay_s: float = 0.5) -> bytes:
        chunk_size = max(1, min(chunk_size, 512))
        data = bytearray()
        while len(data) < total_len:
            remaining = total_len - len(data)
            chunk = self.read_picture_chunk(
                min(chunk_size, remaining),
                retries=chunk_retries,
                retry_delay_s=retry_delay_s,
            )
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data)
