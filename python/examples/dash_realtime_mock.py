#!/usr/bin/env python3
"""
Realtime Plotly Dash mock line chart with rolling background bands.

Run:
    pip install dash plotly
    python dash_realtime_mock.py

Open in browser:
    http://127.0.0.1:8050
"""

from __future__ import annotations

import base64
import random
from collections import deque
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dash import Dash, Input, Output, dcc, html
import plotly.graph_objects as go


WINDOW_SECONDS = 60
UPDATE_MS = 500
MAX_POINTS = WINDOW_SECONDS * 4
BACKGROUND_BAND_SECONDS = 5


timestamps: deque[datetime] = deque(maxlen=MAX_POINTS)
values: deque[float] = deque(maxlen=MAX_POINTS)


def load_background_image() -> str | None:
    """Load capture.jpg near this script and return a data URI."""
    image_path = Path(__file__).with_name("capture.jpg")
    if not image_path.exists():
        return None

    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


BACKGROUND_IMAGE_URI = load_background_image()


def mock_next_value(previous: float | None) -> float:
    """Generate the next mock point with gentle drift and noise."""
    base = 25.0 if previous is None else previous
    drift = (25.0 - base) * 0.03
    noise = random.uniform(-0.35, 0.35)
    return max(0.0, base + drift + noise)


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
app.title = "Realtime Mock Chart"

chart_container_style = {
    "padding": "8px",
    "borderRadius": "8px",
    "backgroundColor": "transparent",
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
        html.H3("Realtime Mock Sensor (Rolling Window)"),
        html.Div(
            dcc.Graph(
                id="realtime-graph",
                style={"backgroundColor": "transparent"},
            ),
            style=chart_container_style,
        ),
        dcc.Interval(id="tick", interval=UPDATE_MS, n_intervals=0),
    ],
    style={
        "maxWidth": "980px",
        "margin": "24px auto",
        "padding": "0 12px",
        "backgroundColor": "transparent",
    },
)


@app.callback(Output("realtime-graph", "figure"), Input("tick", "n_intervals"))
def update_chart(_: int) -> go.Figure:
    now = datetime.now(timezone.utc)
    previous = values[-1] if values else None
    new_value = mock_next_value(previous)

    timestamps.append(now)
    values.append(new_value)

    window_start = now - timedelta(seconds=WINDOW_SECONDS)
    while timestamps and timestamps[0] < window_start:
        timestamps.popleft()
        values.popleft()

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=list(timestamps),
            y=list(values),
            mode="lines",
            name="Mock value",
            line={"width": 2},
        )
    )

    fig.update_layout(
        template="none",
        margin={"l": 40, "r": 20, "t": 30, "b": 40},
        xaxis={
            "title": "Time (UTC)",
            "range": [window_start, now],
            "showgrid": True,
            "gridcolor": "rgba(255,255,255,0.25)",
            "zeroline": False,
        },
        yaxis={
            "title": "Value",
            "rangemode": "tozero",
            "showgrid": True,
            "gridcolor": "rgba(255,255,255,0.25)",
            "zeroline": False,
        },
        paper_bgcolor="rgba(0,0,0,0)",
        plot_bgcolor="rgba(0,0,0,0)",
        shapes=build_rolling_bands(window_start, now),
        uirevision="fixed",
    )

    return fig


if __name__ == "__main__":
    app.run(debug=True, host="127.0.0.1", port=8050)