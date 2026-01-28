#!/usr/bin/env python3
"""
Test RS485 interface via REST API - similar to rs485_example.c echo test.

This script opens the RS485 interface on the edge device and continuously
reads/writes data, echoing received data back (like the modbus echo example).

Usage:
    python test_rs485_echo.py --base-url http://192.168.10.177:8088 --client B43A45A45A08

    # With custom pins (matching modbus example defaults):
    python test_rs485_echo.py --base-url http://192.168.10.177:8088 --client B43A45A45A08 \
        --tx-pin 17 --rx-pin 18 --baud 9600
"""

import argparse
import sys
import time
import threading
from datetime import datetime, timezone

# REST API imports
from core import Lwm2mRestClient, RestConfig
from core.uart_client import RS485_OBJECT_ID, RS485_RESOURCES, UartSession


# Global state
stop_event = threading.Event()


def format_hex(data: bytes) -> str:
    """Format bytes as hex string."""
    return " ".join(f"0x{b:02X}" for b in data)


def format_text(data: bytes) -> str:
    """Format bytes as printable text (replace non-printable with '.')."""
    return "".join(chr(b) if 0x20 <= b < 0x7F else "." for b in data)


def create_rs485_session(args: argparse.Namespace) -> UartSession:
    """Create and open an RS485 session via REST API."""
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
    print(f"Opening RS485 interface...")
    print(f"  TX Pin: {args.tx_pin}")
    print(f"  RX Pin: {args.rx_pin}")
    print(f"  Baudrate: {args.baud}")
    print(f"  RX Buffer: {args.rx_size}")
    
    session.open(
        baudrate=args.baud,
        tx_pin=args.tx_pin,
        rx_pin=args.rx_pin,
        rx_size=args.rx_size,
        modbus_unit_id=args.unit_id,
        mode=args.rs485_mode,
    )
    
    print("RS485 interface opened successfully!")
    return session


def echo_test_loop(session: UartSession, args: argparse.Namespace) -> None:
    """
    Main echo test loop - similar to rs485_example.c.
    
    Continuously reads from RS485 and prints received data.
    Optionally sends test messages.
    """
    print()
    print("=" * 60)
    print("RS485 Echo Test - Reading continuously...")
    print("Press Ctrl+C to stop")
    print("=" * 60)
    print()
    
    # Send initial test message if requested
    if args.send_test:
        test_msg = "Start RS485 REST API test.\r\n"
        print(f"Sending test message: {repr(test_msg)}")
        try:
            session.write(test_msg.encode())
            print("Test message sent!")
        except Exception as e:
            print(f"Failed to send test message: {e}")
        print()
    
    read_count = 0
    empty_count = 0
    
    while not stop_event.is_set():
        timestamp = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3]
        
        try:
            # Read from RS485
            data = session.read()
            
            if data and len(data) > 0:
                read_count += 1
                empty_count = 0
                
                print(f"[{timestamp}] Received {len(data)} bytes:")
                print(f"  Hex:  [ {format_hex(data)} ]")
                print(f"  Text: \"{format_text(data)}\"")
                
                # Echo back if enabled
                if args.echo:
                    try:
                        # Send prefix + data + suffix like rs485_example.c
                        echo_msg = b"\r\nRS485 Received: [" + data + b"]\r\n"
                        session.write(echo_msg)
                        print(f"  Echoed back!")
                    except Exception as e:
                        print(f"  Echo failed: {e}")
                
                print()
            else:
                empty_count += 1
                
                # Show heartbeat every N empty reads
                if args.show_heartbeat and empty_count >= args.heartbeat_interval:
                    print(f"[{timestamp}] ... waiting for data (reads: {read_count})")
                    empty_count = 0
                    
                    # Send heartbeat dot like rs485_example.c
                    if args.send_heartbeat:
                        try:
                            session.write(b".")
                        except Exception:
                            pass
                            
        except Exception as e:
            print(f"[{timestamp}] Read error: {e}")
            if args.debug:
                import traceback
                traceback.print_exc()
        
        # Poll interval
        time.sleep(args.poll_interval)


def interactive_mode(session: UartSession, args: argparse.Namespace) -> None:
    """Interactive mode - type messages to send."""
    print()
    print("=" * 60)
    print("RS485 Interactive Mode")
    print("Type messages to send, or 'quit' to exit")
    print("=" * 60)
    print()
    
    # Start reader thread
    reader_thread = threading.Thread(
        target=echo_test_loop,
        args=(session, args),
        daemon=True
    )
    reader_thread.start()
    
    try:
        while not stop_event.is_set():
            try:
                user_input = input("> ")
                if user_input.lower() in ("quit", "exit", "q"):
                    break
                
                # Send the message
                msg = (user_input + "\r\n").encode()
                session.write(msg)
                print(f"Sent: {repr(user_input)}")
                
            except EOFError:
                break
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Test RS485 interface via REST API (echo test)"
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
    
    # RS485/UART configuration - defaults match modbus example
    parser.add_argument(
        "--tx-pin",
        type=int,
        default=17,
        help="RS485 TX pin (default: 17, matching modbus example)"
    )
    parser.add_argument(
        "--rx-pin",
        type=int,
        default=18,
        help="RS485 RX pin (default: 18, matching modbus example)"
    )
    parser.add_argument(
        "--baud",
        type=int,
        default=9600,
        help="Baudrate (default: 9600)"
    )
    parser.add_argument(
        "--rx-size",
        type=int,
        default=256,
        help="RX buffer size"
    )
    parser.add_argument(
        "--unit-id",
        type=int,
        default=1,
        help="Modbus unit ID"
    )
    parser.add_argument(
        "--rs485-mode",
        type=int,
        default=0,
        help="RS485 mode value"
    )
    
    # Test options
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.1,
        help="Poll interval in seconds (default: 0.1)"
    )
    parser.add_argument(
        "--echo",
        action="store_true",
        help="Echo received data back (like rs485_example.c)"
    )
    parser.add_argument(
        "--send-test",
        action="store_true",
        help="Send initial test message"
    )
    parser.add_argument(
        "--show-heartbeat",
        action="store_true",
        help="Show periodic heartbeat when no data"
    )
    parser.add_argument(
        "--send-heartbeat",
        action="store_true",
        help="Send '.' heartbeat like rs485_example.c"
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=int,
        default=50,
        help="Empty reads between heartbeat messages"
    )
    parser.add_argument(
        "--interactive",
        "-i",
        action="store_true",
        help="Interactive mode - type messages to send"
    )
    
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    
    print("=" * 60)
    print("RS485 REST API Echo Test")
    print("=" * 60)
    print()
    
    try:
        session = create_rs485_session(args)
    except Exception as e:
        print(f"Failed to create RS485 session: {e}")
        if args.debug:
            import traceback
            traceback.print_exc()
        sys.exit(1)
    
    try:
        if args.interactive:
            interactive_mode(session, args)
        else:
            echo_test_loop(session, args)
    except KeyboardInterrupt:
        print("\nStopping...")
        stop_event.set()
    finally:
        print("Closing RS485 session...")
        try:
            session.close()
        except Exception:
            pass
        print("Done.")


if __name__ == "__main__":
    main()
