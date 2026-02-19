FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements-dash.txt ./
RUN pip install --no-cache-dir -r requirements-dash.txt

COPY . .

EXPOSE 8050

CMD ["python", "examples/dash_realtime_mock.py", "--host", "0.0.0.0", "--port", "8050"]
