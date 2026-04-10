# edge-device-sdk
edge device SDK for EdgeZ devices, leveraging lwm2m and wifi halow to build low power always available devices.

## User Guide

### Web UI
1. Install dependencies: `cd web-ui && npm install`.
2. Connect your computer to the Heltec HT-H7608 device over Wi-Fi.
3. Start the dev server: `npm run serve` (from the `web-ui` folder).
4. Open http://localhost:8088 in your browser.

### Python helpers
- See [python/sensor.py](python/sensor.py) for the full helper and device-specific routines.
- Minimal example to query a sensor via the REST gateway:

```python
from sensor import RestConfig, Lwm2mRestClient, I2CSession, ens210_read

client = Lwm2mRestClient(RestConfig(base_url="http://192.168.100.1:8088"))
endpoint = next(client.list_clients())  # Or set your known endpoint name

session = I2CSession(client, endpoint, instance=0)
session.open(addr=0x43)  # Replace with your sensor's I2C address

readings = ens210_read(session)
print(readings)
```

### VC0706 UART REST examples

Use [python/examples/test_uart_vc0706_rest.py](python/examples/test_uart_vc0706_rest.py) for VC0706 camera tests over the dedicated UART object.

- Capture image with UART pins TX=19 / RX=20 (default):

```bash
cd python/examples
python3 test_uart_vc0706_rest.py --client B43A45A45A2C --base-url http://192.168.10.105:8088 --action capture --output capture_uart.jpg
```

- Get camera version with explicit pins:

```bash
cd python/examples
python3 test_uart_vc0706_rest.py --client B43A45A45A2C --base-url http://192.168.10.105:8088 --action version --tx-pin 19 --rx-pin 20
```

### Modbus RTU REST examples

Use [python/examples/modbus_rtu_rest.py](python/examples/modbus_rtu_rest.py) for RS485 Modbus tests over the REST API.

- Scan device address/baud combinations and stop at first hit:

```bash
cd python/examples
python3 modbus_rtu_rest.py --client B43A45A45A08 --base-url http://192.168.10.105:8088 --action scan --scan-min-unit 1 --scan-max-unit 20 --scan-baud-list 4800,9600,2400
```

- One-shot flow meter read (same decode profile as `collect_flow_modbus.py`):

```bash
cd python/examples
python3 modbus_rtu_rest.py --client B43A45A45A08 --base-url http://192.168.10.105:8088 --action flow --baud 9600 --unit-id 1 --address 0 --count 4
```

- Continuous flow polling:

```bash
cd python/examples
python3 modbus_rtu_rest.py --client B43A45A45A08 --base-url http://192.168.10.105:8088 --action flow-poll --baud 9600 --unit-id 1 --address 0 --count 4 --poll-interval 1.0


python3 test_uart_vc0706_rest.py --client B43A45A45A2C --base-url http://192.168.10.105:8088 --baud 38400 --no-logs
```

Notes:
- `--tx-pin` / `--rx-pin` are optional; if omitted, device-side pin defaults are used.
- For environmental (CO2/temp/humidity/light) profile, use `--action env`.

### Lua helpers

Use [lua/cli.lua](lua/cli.lua) to run Lua SDK scripts over the same REST gateway.

- Get VC0706 camera version:

```bash
cd lua
lua cli.lua -c B43A45A45A2C -u http://192.168.10.105:8088 -s vc0706_camera --action version --baud 115200
```

- Capture image from VC0706 camera:

```bash
cd lua
lua cli.lua -c B43A45A45A2C -u http://192.168.10.105:8088 -s vc0706_camera --action capture --baud 115200 --output capture.jpg
```

- Capture image from VC0706 camera with custom UART pins:

```bash
cd lua
lua cli.lua -c B43A45A45A2C -u http://192.168.10.105:8088 -s vc0706_camera --action capture --baud 115200 --tx-pin 17 --rx-pin 18 --output capture.jpg
```

- Set camera resolution only:

```bash
cd lua
lua cli.lua -c B43A45A45A2C -u http://192.168.10.105:8088 -s vc0706_camera --action set-resolution --baud 115200
```

- Get VC0706-like camera variant version:

```bash
cd lua
lua cli.lua -c B43A45A45A2C -u http://192.168.10.105:8088 -s vc0706_compat_camera --action version --baud 115200
```

- Capture image from VC0706-like camera variant:

```bash
cd lua
lua cli.lua -c B43A45A45A2C -u http://192.168.10.105:8088 -s vc0706_compat_camera --action capture --baud 115200 --output capture.jpg
```

- Capture image from VC0706-like camera variant with custom UART pins:

```bash
cd lua
lua cli.lua -c B43A45A45A2C -u http://192.168.10.105:8088 -s vc0706_compat_camera --action capture --baud 115200 --tx-pin 17 --rx-pin 18 --output capture.jpg
```

- Set VC0706-like camera variant resolution only:

```bash
cd lua
lua cli.lua -c B43A45A45A2C -u http://192.168.10.105:8088 -s vc0706_compat_camera --action set-resolution --baud 115200
```

- Configure VC0706-like camera variant (pins + protocol options) and capture:

```bash
cd lua
lua cli.lua -c B43A45A45A2C -u http://192.168.10.105:8088 -s vc0706_compat_camera --action capture --baud 115200 --tx-pin 19 --rx-pin 20 --cam-serial-num 0x00 --cam-set-res-cmd 0x54 --cam-res-code 0x55 --cam-read-chunk-size 128 --output capture.jpg
```

## License
- The `web-ui` package is licensed under the Eclipse Public License 2.0 (EPL-2.0).
- All other parts of the repository are licensed under the Apache License 2.0.


python3 cli.py --base-url http://192.168.10.177:8088 --client B43A45A45A08 vc0706 \
  --iface uart --tx-pin 20 --rx-pin 19 --baud 38400 \
  --action capture --output vc0706.jpg --chunk-size 255 --reset-before --resume --retries 3 --retry-delay 1.0


  python3 cli.py --base-url http://192.168.10.177:8088 --client B43A45A45A08 vc0706 \
  --iface uart --action capture --output vc0706.jpg --chunk-size 255 --resume --retries 3 --retry-delay 1.0


  python3 cli.py --base-url http://192.168.10.105:8088 --client B43A45A45A08 --timeout 15 flow --iface rs485 --tx-pin 17 --rx-pin 18 --baud 9600 --unit-id 1 --address 0 --count 4 --poll-interval 1.0


  python3 cli.py --base-url http://192.168.10.105:8088 --client B43A45A45A08 env485 --iface rs485 --tx-pin 17 --rx-pin 18 --baud 4800 --unit-id 1 --address 0 --count 5 --light-range 200000 --poll-interval 1.0


  python3 cli.py --base-url http://192.168.10.105:8088 --client B43A45A45A08 env485 --iface rs485 --baud 4800 --unit-id 1 --count-limit 1



  python3 cli.py --base-url http://192.168.10.177:8088 --client B43A45A45A08 vc0706 \
  --iface rs485 --tx-pin 17 --rx-pin 18 --baud 115200 \
  --action capture --output vc0706.jpg --chunk-size 255 --reset-before --resume --retries 3 --retry-delay 1.0





python3 cli.py --client B43A45A45A2C --base-url http://192.168.10.177:8088   sht3x


python3 cli.py --client B43A45A45A08 --base-url http://192.168.10.177:8088   ens210

 

 python3 cli.py --base-url https://12d3koowq7bep4gquw19awbudgsbddha75jfj5tfsbgetbstsegw.demos.edgez.biz --client B43A45A459C8 modbus-th --baud 9600 --unit-id 1 --address 0 --count 1 --function-code 3 --temp-scale 10 --count-limit 1