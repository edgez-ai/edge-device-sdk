from __future__ import annotations

import argparse
import time
from datetime import datetime
from typing import Optional, Sequence

from core import I2CSession, Lwm2mRestClient, RestConfig, pick_client
from driver import read_ens210, read_sht3x


def parse_byte_list(text: str) -> Sequence[int]:
    parts = [p.strip() for p in text.split(",") if p.strip()]
    return [int(part, 0) for part in parts]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Drive I2C via LwM2M REST gateway")
    parser.add_argument("--base-url", default="http://192.168.100.1:8088", help="REST gateway base URL")
    parser.add_argument("--client", help="LwM2M endpoint name; defaults to the only registered client")
    parser.add_argument("--instance", type=int, default=0, help="I2C object instance id")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout in seconds")
    parser.add_argument("--verbose", action="store_true", help="Ignored; kept for backward compatibility")

    mode = parser.add_subparsers(dest="mode", required=False)

    p_ens = mode.add_parser("ens210", help="Read ENS210 temperature/humidity")
    p_ens.add_argument("--addr", type=lambda x: int(x, 0), default=0x43, help="I2C address (default 0x43)")
    p_ens.add_argument("--delay", type=float, default=0.15, help="Measurement wait time seconds")
    p_ens.add_argument("--interval", type=float, default=0.0, help="Poll interval seconds; 0 for one-shot")
    p_ens.add_argument("--count", type=int, default=0, help="Number of polls when interval>0; 0 for forever")

    p_sht3x = mode.add_parser("sht3x", help="Read SHT3x temperature/humidity")
    p_sht3x.add_argument("--addr", type=lambda x: int(x, 0), default=0x44, help="I2C address (default 0x44)")
    p_sht3x.add_argument("--repeatability", choices=["high", "med", "low"], default="high", help="Measurement repeatability")
    p_sht3x.add_argument("--delay", type=float, default=0.001, help="Measurement wait time seconds")
    p_sht3x.add_argument("--interval", type=float, default=0.0, help="Poll interval seconds; 0 for one-shot")
    p_sht3x.add_argument("--count", type=int, default=0, help="Number of polls when interval>0; 0 for forever")

    p_raw = mode.add_parser("raw", help="Manual I2C transaction")
    p_raw.add_argument("--addr", type=lambda x: int(x, 0), required=True, help="I2C address")
    p_raw.add_argument("--write", type=str, required=True, help="Bytes to write, e.g. 0x22,0x03 or 34,3")
    p_raw.add_argument("--read", type=int, default=0, help="Bytes to read after write")

    return parser


def run_raw(args: argparse.Namespace, session: I2CSession, client: Lwm2mRestClient, endpoint: str) -> None:
    session.open(args.addr)
    if args.read > 0:
        client.i2c_set_rx_size(endpoint, args.instance, args.read)
    session.write(parse_byte_list(args.write))
    time.sleep(0.01)
    data = b""
    if args.read > 0:
        data = session.read()[: args.read]
    print({"time": datetime.utcnow().isoformat(), "endpoint": endpoint, "raw_read_hex": data.hex(), "raw_read_len": len(data)})


def run_ens210(args: argparse.Namespace, session: I2CSession, endpoint: str) -> None:
    addr = getattr(args, "addr", 0x43)
    delay = getattr(args, "delay", 0.15)
    interval = getattr(args, "interval", 0.0)
    count = getattr(args, "count", 0)
    session.open(addr)

    def _once() -> None:
        result = read_ens210(session, delay_s=delay)
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


def run_sht3x(args: argparse.Namespace, session: I2CSession, endpoint: str) -> None:
    addr = getattr(args, "addr", 0x44)
    repeatability = getattr(args, "repeatability", "high")
    delay = getattr(args, "delay", 0.001)
    interval = getattr(args, "interval", 0.0)
    count = getattr(args, "count", 0)
    session.open(addr)

    def _once() -> None:
        result = read_sht3x(session, repeatability=repeatability, delay_s=delay)
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


def main(argv: Optional[Sequence[str]] = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = RestConfig(base_url=args.base_url, timeout=args.timeout)
    client = Lwm2mRestClient(config)
    endpoint = pick_client(client, args.client)
    session = I2CSession(client, endpoint, args.instance)

    if args.mode == "raw":
        run_raw(args, session, client, endpoint)
    elif args.mode == "sht3x":
        run_sht3x(args, session, endpoint)
    else:
        run_ens210(args, session, endpoint)


if __name__ == "__main__":
    main()
