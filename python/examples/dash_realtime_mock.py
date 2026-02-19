#!/usr/bin/env python3
"""
Realtime Dash dashboard using live device data:
- VC0706 camera image over REST (RS485) as chart background
- SHT3x temperature/humidity over REST (I2C) as realtime chart

Run:
    pip install dash plotly requests
    python dash_realtime_mock.py --client <ENDPOINT> --base-url <URL>

Open in browser:
    http://127.0.0.1:8050
"""

from __future__ import annotations

import base64
import argparse
import json
import math
import sys
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dash import Dash, Input, Output, dcc, html
from flask import Response
import plotly.graph_objects as go

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from core import I2CSession, Lwm2mRestClient, RS485_OBJECT_ID, RS485_RESOURCES, RestConfig, UartSession, pick_client
from driver import VC0706Camera, read_sht3x


WINDOW_SECONDS = 6 * 60 * 60
UI_UPDATE_MS = 1000
SHT3X_INTERVAL_S = 60.0
CAMERA_INTERVAL_S = 1.5
MAX_POINTS = int(WINDOW_SECONDS / SHT3X_INTERVAL_S) + 1
BACKGROUND_BAND_SECONDS = 5
SHT3X_HISTORY_FILE = Path(__file__).with_name("sht3x_history.jsonl")


class SharedState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.timestamps: deque[datetime] = deque(maxlen=MAX_POINTS)
        self.temperatures: deque[float] = deque(maxlen=MAX_POINTS)
        self.humidities: deque[float] = deque(maxlen=MAX_POINTS)
        self.latest_camera_jpeg: bytes | None = None
        self.latest_camera_ts: str = "0"


STATE = SharedState()
STOP_EVENT = threading.Event()


def load_background_image() -> str | None:
    """Load capture.jpg near this script and return a data URI."""
    image_path = Path(__file__).with_name("capture.jpg")
    if not image_path.exists():
        return None

    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


BACKGROUND_IMAGE_URI = load_background_image()


def is_valid_sensor_value(value: object) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def load_recent_sht3x_history(window_seconds: int) -> list[tuple[datetime, float, float]]:
    if not SHT3X_HISTORY_FILE.exists():
        return []

    cutoff = datetime.now(timezone.utc) - timedelta(seconds=window_seconds)
    result: list[tuple[datetime, float, float]] = []

    try:
        for line in SHT3X_HISTORY_FILE.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue

            payload = json.loads(line)
            ts_text = payload.get("ts")
            temp = payload.get("temperature_c")
            hum = payload.get("humidity_rh")
            if ts_text is None or temp is None or hum is None:
                continue
            if not is_valid_sensor_value(temp) or not is_valid_sensor_value(hum):
                continue

            timestamp = datetime.fromisoformat(ts_text)
            if timestamp.tzinfo is None:
                timestamp = timestamp.replace(tzinfo=timezone.utc)

            if timestamp >= cutoff:
                result.append((timestamp, float(temp), float(hum)))
    except Exception:
        return []

    return result


def compact_sht3x_history(window_seconds: int) -> None:
    recent = load_recent_sht3x_history(window_seconds)
    tmp_path = SHT3X_HISTORY_FILE.with_suffix(".jsonl.tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for ts, temp, hum in recent:
            handle.write(
                json.dumps({"ts": ts.isoformat(), "temperature_c": temp, "humidity_rh": hum}) + "\n"
            )
    tmp_path.replace(SHT3X_HISTORY_FILE)


def persist_sht3x_sample(timestamp: datetime, temperature: float, humidity: float, window_seconds: int) -> None:
    if not is_valid_sensor_value(temperature) or not is_valid_sensor_value(humidity):
        return

    payload = {
        "ts": timestamp.isoformat(),
        "temperature_c": temperature,
        "humidity_rh": humidity,
    }
    with SHT3X_HISTORY_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload) + "\n")
    compact_sht3x_history(window_seconds)


def bootstrap_sht3x_history(window_seconds: int) -> None:
    history = load_recent_sht3x_history(window_seconds)
    if not history:
        return

    with STATE.lock:
        STATE.timestamps.clear()
        STATE.temperatures.clear()
        STATE.humidities.clear()
        for ts, temp, hum in history:
            STATE.timestamps.append(ts)
            STATE.temperatures.append(temp)
            STATE.humidities.append(hum)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="VC0706 + SHT3x Dash dashboard via REST")
    parser.add_argument("--base-url", default="http://192.168.100.1:8088", help="LwM2M REST base URL")
    parser.add_argument("--client", help="LwM2M endpoint; auto-picks if only one client exists")
    parser.add_argument("--instance", type=int, default=0, help="LwM2M object instance id")
    parser.add_argument("--timeout", type=float, default=5.0, help="HTTP timeout seconds")

    parser.add_argument("--sht3x-addr", type=lambda x: int(x, 0), default=0x44, help="SHT3x I2C address")
    parser.add_argument("--sht3x-repeatability", choices=["high", "med", "low"], default="high")
    parser.add_argument("--sht3x-delay", type=float, default=0.001, help="SHT3x conversion delay seconds")
    parser.add_argument("--sht3x-interval", type=float, default=SHT3X_INTERVAL_S, help="SHT3x polling interval seconds")

    parser.add_argument("--camera-baud", type=int, default=115200, help="VC0706 RS485 baudrate")
    parser.add_argument("--camera-tx-pin", type=int, default=None, help="RS485 TX pin")
    parser.add_argument("--camera-rx-pin", type=int, default=None, help="RS485 RX pin")
    parser.add_argument("--camera-rx-size", type=int, default=4096, help="RS485 RX buffer size")
    parser.add_argument("--camera-serial", type=int, default=0, help="VC0706 serial id")
    parser.add_argument("--camera-chunk", type=int, default=128, help="Camera read chunk size")
    parser.add_argument("--camera-retries", type=int, default=1, help="Per-chunk retry count")
    parser.add_argument("--camera-interval", type=float, default=CAMERA_INTERVAL_S, help="Delay between camera captures")

    parser.add_argument("--window-seconds", type=int, default=WINDOW_SECONDS, help="Chart rolling window")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8050)
    parser.add_argument("--debug-io", action="store_true", help="Enable low-level UART debug logs")
    return parser.parse_args()


def init_client(args: argparse.Namespace) -> tuple[Lwm2mRestClient, str]:
    client = Lwm2mRestClient(RestConfig(base_url=args.base_url, timeout=args.timeout))
    endpoint = pick_client(client, args.client)
    return client, endpoint


def capture_camera_frame(camera: VC0706Camera, args: argparse.Namespace) -> bytes | None:
    if not camera.take_picture():
        return None

    try:
        length = camera.frame_length()
        if length <= 0 or length > 500000:
            return None
        data = camera.read_picture(
            length,
            chunk_size=max(1, min(args.camera_chunk, 512)),
            chunk_retries=max(0, args.camera_retries),
            retry_delay_s=0.3,
        )
        if not data:
            return None
        return data
    finally:
        camera.resume_video()


def camera_loop(args: argparse.Namespace) -> None:
    while not STOP_EVENT.is_set():
        try:
            client, endpoint = init_client(args)
            session = UartSession(
                client,
                endpoint,
                args.instance,
                object_id=RS485_OBJECT_ID,
                resources=RS485_RESOURCES,
                debug=args.debug_io,
            )
            session.open(
                baudrate=args.camera_baud,
                tx_pin=args.camera_tx_pin,
                rx_pin=args.camera_rx_pin,
                rx_size=args.camera_rx_size,
            )
            camera = VC0706Camera(
                session,
                serial_num=args.camera_serial,
                timeout_s=max(0.2, args.timeout),
                debug=args.debug_io,
            )

            while not STOP_EVENT.is_set():
                started = time.monotonic()
                image = capture_camera_frame(camera, args)
                if image:
                    with STATE.lock:
                        STATE.latest_camera_jpeg = image
                        STATE.latest_camera_ts = str(int(time.time() * 1000))

                elapsed = time.monotonic() - started
                delay = max(0.0, args.camera_interval - elapsed)
                STOP_EVENT.wait(delay)

            session.close()
            return
        except Exception:
            STOP_EVENT.wait(2.0)


def sht3x_loop(args: argparse.Namespace) -> None:
    while not STOP_EVENT.is_set():
        started = time.monotonic()
        try:
            client, endpoint = init_client(args)
            session = I2CSession(client, endpoint, args.instance)
            session.open(args.sht3x_addr)
            result = read_sht3x(
                session,
                repeatability=args.sht3x_repeatability,
                delay_s=args.sht3x_delay,
            )
            temperature = result.get("temperature_c")
            humidity = result.get("humidity_rh")
            if (
                temperature is not None
                and humidity is not None
                and is_valid_sensor_value(temperature)
                and is_valid_sensor_value(humidity)
            ):
                now = datetime.now(timezone.utc)
                temperature_f = float(temperature)
                humidity_f = float(humidity)
                with STATE.lock:
                    STATE.timestamps.append(now)
                    STATE.temperatures.append(temperature_f)
                    STATE.humidities.append(humidity_f)
                try:
                    persist_sht3x_sample(
                        now,
                        temperature_f,
                        humidity_f,
                        int(getattr(args, "window_seconds", WINDOW_SECONDS)),
                    )
                except Exception:
                    pass
        except Exception:
            pass

        elapsed = time.monotonic() - started
        delay = max(0.0, args.sht3x_interval - elapsed)
        STOP_EVENT.wait(delay)


def build_rolling_bands(start_time: datetime, end_time: datetime) -> list[dict]:
    """Build alternating vertical bands that move with the realtime x-range."""
    shapes: list[dict] = []
    total_seconds = (end_time - start_time).total_seconds()
    band_count = int(total_seconds // BACKGROUND_BAND_SECONDS) + 3

    anchor = start_time - timedelta(seconds=start_time.second % BACKGROUND_BAND_SECONDS,
                                    microseconds=start_time.microsecond)

    for index in range(band_count):
        x0 = anchor + timedelta(seconds=index * BACKGROUND_BAND_SECONDS)
        x1 = x0 + timedelta(seconds=BACKGROUND_BAND_SECONDS)
        if index % 2 == 0:
            shapes.append(
                {
                    "type": "rect",
                    "xref": "x",
                    "yref": "paper",
                    "x0": x0,
                    "x1": x1,
                    "y0": 0,
                    "y1": 1,
                    "fillcolor": "rgba(120, 120, 120, 0.08)",
                    "line": {"width": 0},
                    "layer": "below",
                }
            )

    return shapes


app = Dash(__name__)
app.title = "VC0706 + SHT3x Dashboard"


@app.server.route("/camera.jpg")
def camera_image() -> Response:
    with STATE.lock:
        image = STATE.latest_camera_jpeg
    if image:
        return Response(image, mimetype="image/jpeg")

    fallback_path = Path(__file__).with_name("capture.jpg")
    if fallback_path.exists():
        try:
            return Response(fallback_path.read_bytes(), mimetype="image/jpeg")
        except Exception:
            pass

    return Response(status=404)

chart_container_style = {
    "padding": "0",
    "borderRadius": "0",
    "backgroundColor": "transparent",
    "position": "fixed",
    "top": 0,
    "left": 0,
    "width": "100vw",
    "height": "100vh",
    "overflow": "hidden",
}

if BACKGROUND_IMAGE_URI:
    chart_container_style.update(
        {
            "backgroundImage": f"url({BACKGROUND_IMAGE_URI})",
            "backgroundSize": "cover",
            "backgroundPosition": "center",
            "backgroundRepeat": "no-repeat",
        }
    )

app.layout = html.Div(
    [
        html.Div(
            dcc.Graph(
                id="realtime-graph",
                style={
                    "backgroundColor": "transparent",
                    "position": "fixed",
                    "top": 0,
                    "left": 0,
                    "width": "100vw",
                    "height": "100vh",
                },
            ),
            id="chart-container",
            style=chart_container_style,
        ),
        dcc.Interval(id="tick", interval=UI_UPDATE_MS, n_intervals=0),
    ],
    style={
        "position": "fixed",
        "top": 0,
        "left": 0,
        "width": "100vw",
        "height": "100vh",
        "margin": 0,
        "padding": 0,
        "overflow": "hidden",
        "backgroundColor": "transparent",
    },
)


@app.callback(
    Output("realtime-graph", "figure"),
    Output("chart-container", "style"),
    Input("tick", "n_intervals"),
)
def update_chart(_: int) -> tuple[go.Figure, dict]:
    now = datetime.now(timezone.utc)
    window_seconds = getattr(APP_ARGS, "window_seconds", WINDOW_SECONDS)
    window_start = now - timedelta(seconds=window_seconds)

    with STATE.lock:
        points = [
            (timestamp, temp, hum)
            for timestamp, temp, hum in zip(STATE.timestamps, STATE.temperatures, STATE.humidities)
            if timestamp >= window_start and is_valid_sensor_value(temp) and is_valid_sensor_value(hum)
        ]
        camera_ts = STATE.latest_camera_ts

    fig = go.Figure()
    if points:
        xs = [item[0] for item in points]
        temps = [item[1] for item in points]
        hums = [item[2] for item in points]
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=temps,
                mode="lines+markers",
                name="Temperature (°C)",
                line={"width": 2},
            )
        )
        fig.add_trace(
            go.Scatter(
                x=xs,
                y=hums,
                mode="lines+markers",
                name="Humidity (%RH)",
                line={"width": 2},
                yaxis="y2",
            )
        )

    fig.update_layout(
        template="none",
        margin={"l": 0, "r": 0, "t": 0, "b": 0},
        autosize=True,
        xaxis={
            "title": "Time (UTC)",
            "type": "date",
            "range": [window_start, now],
            "showgrid": True,
            "gridcolor": "rgba(255,255,255,0.25)",
            "zeroline": False,
        },
        yaxis={
            "title": "Temperature (°C)",
            "range": [-30, 70],
            "showgrid": True,
            "gridcolor": "rgba(255,255,255,0.25)",
            "zeroline": False,
        },
        yaxis2={
            "title": "Humidity (%RH)",
            "overlaying": "y",
            "side": "right",
            "range": [0, 100],
            "showgrid": False,
            "zeroline": False,
        },
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        shapes=build_rolling_bands(window_start, now) if points else [],
        uirevision="fixed",
        legend={"orientation": "h", "y": 1.1, "x": 0},
    )

    container_style = dict(chart_container_style)
    if camera_ts != "0":
        container_style.update(
            {
                "backgroundImage": f"url('/camera.jpg?ts={camera_ts}')",
                "backgroundSize": "cover",
                "backgroundPosition": "center",
                "backgroundRepeat": "no-repeat",
            }
        )
    elif BACKGROUND_IMAGE_URI:
        container_style.update(
            {
                "backgroundImage": f"url({BACKGROUND_IMAGE_URI})",
                "backgroundSize": "cover",
                "backgroundPosition": "center",
                "backgroundRepeat": "no-repeat",
            }
        )

    return fig, container_style


if __name__ == "__main__":
    APP_ARGS = parse_args()
    bootstrap_sht3x_history(APP_ARGS.window_seconds)

    sensor_thread = threading.Thread(target=sht3x_loop, args=(APP_ARGS,), daemon=True)
    camera_thread = threading.Thread(target=camera_loop, args=(APP_ARGS,), daemon=True)
    sensor_thread.start()
    camera_thread.start()

    try:
        app.run(debug=False, host=APP_ARGS.host, port=APP_ARGS.port)
    finally:
        STOP_EVENT.set()