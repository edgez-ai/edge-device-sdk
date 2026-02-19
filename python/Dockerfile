FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    BASE_URL=http://192.168.100.1:8088 \
    CLIENT= \
    DATA_DIR=/data

WORKDIR /app

COPY requirements-dash.txt ./
RUN pip install --no-cache-dir -r requirements-dash.txt

COPY . .

RUN mkdir -p /data

VOLUME ["/data"]

EXPOSE 8050

CMD ["sh", "-c", "python examples/dash_realtime_mock.py --host 0.0.0.0 --port 8050 --base-url \"$BASE_URL\" ${CLIENT:+--client \"$CLIENT\"}"]
