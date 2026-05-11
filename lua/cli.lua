#!/usr/bin/env lua

local function add_script_path()
  local script = (arg and arg[0]) or ""
  local dir = script:match("^(.*)/[^/]+$")
  if dir and dir ~= "" then
    package.path = dir .. "/?.lua;" .. package.path
  end
end

add_script_path()

local RS485 = require("rs485_interface")
local UART = require("uart_interface")
local I2C = require("i2c_interface")
local Util = require("util")

local cfg_defaults = {
  script = "flow",
  backend = "rest",
  client = nil,
  base_url = nil,
  instance = 0,
  baud = 9600,
  tx_pin = nil,
  rx_pin = nil,
  unit_id = 1,
  address = 0,
  count = 4,
  rx_size = 256,
  rs485_mode = 0,
  modbus_timeout = 1.0,
  rs485_rx_chunk_timeout = 0.5,
  group = "basic",
  flow_scale = 100000.0,
  volume_scale = 10000.0,
  i2c_address = 0x44,
  i2c_rx_size = 16,
  action = "capture",
  output = "capture.jpg",
  reset = false,
  quiet = false,
  http_timeout = 5.0,
  cam_serial_num = nil,
  cam_set_resolution_cmd = nil,
  cam_resolution_code = nil,
  cam_read_chunk_size = nil,
}

-- Scripts that use I2C interface instead of RS485
local i2c_scripts = {
  sht3x_temp = true,
}

local function parse_args(argv)
  local cfg = {}
  for k, v in pairs(cfg_defaults) do
    cfg[k] = v
  end
  local script_set = false

  local i = 1
  while i <= #argv do
    local a = argv[i]
    local function next_value()
      i = i + 1
      return argv[i]
    end

    if a == "--client" or a == "-c" then
      cfg.client = next_value()
    elseif a == "--base-url" or a == "-u" then
      cfg.base_url = next_value()
    elseif a == "--backend" then
      cfg.backend = next_value()
    elseif a == "--script" or a == "-s" then
      cfg.script = next_value()
      script_set = true
    elseif a == "--action" then
      cfg.action = next_value()
    elseif a == "--output" or a == "-o" then
      cfg.output = next_value()
    elseif a == "--reset" then
      cfg.reset = true
    elseif a == "--quiet" or a == "-q" then
      cfg.quiet = true
    elseif a == "--cam-serial-num" then
      cfg.cam_serial_num = tonumber(next_value())
    elseif a == "--cam-set-res-cmd" then
      cfg.cam_set_resolution_cmd = tonumber(next_value())
    elseif a == "--cam-res-code" then
      cfg.cam_resolution_code = tonumber(next_value())
    elseif a == "--cam-read-chunk-size" then
      cfg.cam_read_chunk_size = tonumber(next_value())
    elseif a == "--baud" or a == "-b" then
      cfg.baud = tonumber(next_value())
    elseif a == "--unit-id" then
      cfg.unit_id = tonumber(next_value())
    elseif a == "--address" then
      cfg.address = tonumber(next_value())
    elseif a == "--count" then
      cfg.count = tonumber(next_value())
    elseif a == "--rx-size" then
      cfg.rx_size = tonumber(next_value())
    elseif a == "--rs485-mode" then
      cfg.rs485_mode = tonumber(next_value())
    elseif a == "--modbus-timeout" then
      cfg.modbus_timeout = tonumber(next_value())
    elseif a == "--rs485-rx-chunk-timeout" then
      cfg.rs485_rx_chunk_timeout = tonumber(next_value())
    elseif a == "--group" then
      cfg.group = next_value()
    elseif a == "--instance" or a == "-i" then
      cfg.instance = tonumber(next_value())
    elseif a == "--tx-pin" then
      cfg.tx_pin = tonumber(next_value())
    elseif a == "--rx-pin" then
      cfg.rx_pin = tonumber(next_value())
    elseif a == "--i2c-address" then
      cfg.i2c_address = tonumber(next_value())
    elseif a == "--help" or a == "-h" then
      cfg.help = true
    elseif type(a) == "string" and a:sub(1, 1) ~= "-" and not script_set then
      cfg.script = a
      script_set = true
    else
      io.stderr:write("Unknown argument: " .. tostring(a) .. "\n")
      cfg.invalid = true
    end
    i = i + 1
  end

  return cfg
end

local function print_usage()
  print("Usage:")
  print("  lua lua/cli.lua --client <ENDPOINT> --base-url <URL> [OPTIONS]")
  print("")
  print("Options:")
  print("  --client, -c <ENDPOINT>   LwM2M client endpoint name")
  print("  --base-url, -u <URL>      LwM2M server REST API base URL")
  print("  --script, -s <NAME>       Sensor script to run (default: flow)")
  print("  --backend <TYPE>          Backend type: rest|native|auto (default: rest)")
  print("  --action <NAME>           Script action (e.g., version|capture|set-resolution)")
  print("  --group <NAME>            Vibration group: basic|all|full-block|accel|velocity|temp|displacement|frequency|x-advanced|y-advanced|z-advanced")
  print("  --output, -o <FILE>       Output file for capture scripts (default: capture.jpg)")
  print("  --reset                   Reset device/camera before action")
  print("  --cam-serial-num <N>      Camera protocol serial number (for vc0706_compat_camera)")
  print("  --cam-set-res-cmd <N>     Camera set-resolution command code (default: 0x54)")
  print("  --cam-res-code <N>        Camera resolution code (default: 0x55)")
  print("  --cam-read-chunk-size <N> Camera read chunk size in bytes (for vc0706_compat_camera)")
  print("  --baud, -b <RATE>         UART baud rate (default: 9600)")
  print("  --unit-id <N>             Modbus unit id (default: 1, vibration sensor commonly 80/0x50)")
  print("  --address <N>             Modbus register start address (for raw reads)")
  print("  --count <N>               Modbus register count (for raw reads)")
  print("  --rx-size <N>             RS485 RX buffer size (default: 256)")
  print("  --rs485-mode <N>          RS485 mode resource value (default: 0)")
  print("  --modbus-timeout <SEC>    Modbus timeout in seconds (default: 1.0)")
  print("  --rs485-rx-chunk-timeout <SEC> Timeout for RS485 rx chunk poll requests (default: 0.5)")
  print("  --instance, -i <ID>       LwM2M object instance (default: 0)")
  print("  --tx-pin <PIN>            Optional TX pin override")
  print("  --rx-pin <PIN>            Optional RX pin override")
  print("  --i2c-address <ADDR>      I2C device address as decimal (default: 68 = 0x44)")
  print("  --quiet, -q               Suppress script debug output")
  print("  --help, -h                Show this help")
  print("")
  print("Available scripts:")
  print("  flow          Flow meter via RS485/Modbus")
  print("  vibration     Vibration sensor via RS485/Modbus")
  print("  sht3x_temp    SHT3x temperature & humidity via I2C")
  print("  vc0706_camera VC0706 camera over RS485 (version/capture/set-resolution)")
  print("  vc0706_compat_camera VC0706-like UART camera variant (version/capture/set-resolution)")
  print("")
  print("Examples:")
  print("  lua lua/cli.lua --client B43A45A45A08 --base-url http://192.168.10.105:8088")
  print("  lua lua/cli.lua -c B43A45A45A08 -u http://192.168.10.105:8088 -s sht3x_temp")
  print("  lua lua/cli.lua -c B43A45A45A08 -u http://192.168.10.105:8088 -s vc0706_camera --action version --baud 115200")
  print("  lua lua/cli.lua -c B43A45A45A08 -u http://192.168.10.105:8088 -s vc0706_compat_camera --action capture --baud 115200")
  print("  lua lua/cli.lua -c B43A45A45A08 -u http://192.168.10.105:8088 -s vc0706_compat_camera --action capture --tx-pin 19 --rx-pin 20 --cam-set-res-cmd 0x54 --cam-res-code 0x55")
end

local function table_keys_sorted(tbl)
  local keys = {}
  for k in pairs(tbl) do
    keys[#keys + 1] = tostring(k)
  end
  table.sort(keys)
  return keys
end

local function persist_script_buffers(result, cfg, script_name)
  local has_buffer = false
  for _, item in ipairs(result) do
    if type(item) == "table" and item.persist_buffer == true then
      has_buffer = true
      local output_path = item.output or cfg.output

      if type(util_get_global_buffer) ~= "function" then
        return false, "util_get_global_buffer is not available"
      end

      local payload = util_get_global_buffer(true)
      if type(payload) ~= "string" or #payload == 0 then
        return false, "global buffer is empty"
      end

      local f, io_err = io.open(output_path, "wb")
      if not f then
        return false, "failed to open output file '" .. tostring(output_path) .. "': " .. tostring(io_err)
      end
      f:write(payload)
      f:close()

      item.output = output_path
      item.bytes = #payload
      item.persisted = true

      print("Saved global buffer to " .. tostring(output_path) .. " (" .. tostring(#payload) .. " bytes)")
    end
  end

  if not has_buffer then
    return true
  end
  return true
end

local function main()
  local cfg = parse_args(arg)
  if cfg.help then
    print_usage()
    return 0
  end
  if cfg.invalid then
    print_usage()
    return 1
  end

  if not cfg.client or cfg.client == "" or not cfg.base_url or cfg.base_url == "" then
    io.stderr:write("--client and --base-url are required\n\n")
    print_usage()
    return 1
  end

  local script_name = cfg.script or "flow"
  local use_i2c = i2c_scripts[script_name]

  -- Set up I2C globals
  _G.I2C_BACKEND = cfg.backend
  _G.I2C_BASE_URL = cfg.base_url
  _G.I2C_CLIENT = cfg.client
  _G.I2C_INSTANCE = cfg.instance
  _G.I2C_ADDRESS = cfg.i2c_address
  _G.I2C_RX_SIZE = cfg.i2c_rx_size
  _G.I2C_TX_PIN = cfg.tx_pin
  _G.I2C_RX_PIN = cfg.rx_pin
  _G.I2C_HTTP_TIMEOUT = cfg.http_timeout

  -- Set up RS485 globals
  _G.RS485_BACKEND = cfg.backend
  _G.RS485_BASE_URL = cfg.base_url
  _G.RS485_CLIENT = cfg.client
  _G.RS485_INSTANCE = cfg.instance
  _G.RS485_BAUD = cfg.baud
  _G.RS485_RX_SIZE = cfg.rx_size
  _G.RS485_MODE = cfg.rs485_mode
  _G.RS485_UNIT_ID = cfg.unit_id
  _G.RS485_ADDRESS = cfg.address
  _G.RS485_COUNT = cfg.count
  _G.RS485_MODBUS_TIMEOUT = cfg.modbus_timeout
  _G.RS485_RX_CHUNK_TIMEOUT = cfg.rs485_rx_chunk_timeout
  _G.RS485_TX_PIN = cfg.tx_pin
  _G.RS485_RX_PIN = cfg.rx_pin
  _G.RS485_HTTP_TIMEOUT = cfg.http_timeout

  _G.VIBRATION_ACTION = cfg.action
  _G.VIBRATION_GROUP = cfg.group
  _G.VIBRATION_QUIET = cfg.quiet

  -- Set up UART globals
  _G.UART_BACKEND = cfg.backend
  _G.UART_BASE_URL = cfg.base_url
  _G.UART_CLIENT = cfg.client
  _G.UART_INSTANCE = cfg.instance
  _G.UART_BAUD = cfg.baud
  _G.UART_RX_SIZE = cfg.rx_size
  _G.UART_MODE = cfg.rs485_mode
  _G.UART_TX_PIN = cfg.tx_pin
  _G.UART_RX_PIN = cfg.rx_pin
  _G.UART_HTTP_TIMEOUT = cfg.http_timeout

  _G.VC0706_ACTION = cfg.action
  _G.VC0706_OUTPUT = cfg.output
  _G.VC0706_RESET = cfg.reset
  _G.VC0706_BAUD = cfg.baud
  _G.VC0706_QUIET = cfg.quiet

  _G.VC0706_COMPAT_ACTION = cfg.action
  _G.VC0706_COMPAT_OUTPUT = cfg.output
  _G.VC0706_COMPAT_RESET = cfg.reset
  _G.VC0706_COMPAT_BAUD = cfg.baud
  _G.VC0706_COMPAT_QUIET = cfg.quiet

  _G.UART_CAM_SERIAL_NUM = cfg.cam_serial_num
  _G.UART_CAM_CMD_SET_RESOLUTION = cfg.cam_set_resolution_cmd
  _G.UART_CAM_RESOLUTION_CODE = cfg.cam_resolution_code
  _G.UART_CAM_READ_CHUNK_SIZE = cfg.cam_read_chunk_size

  package.loaded[script_name] = nil
  local ok, result_or_err = pcall(require, script_name)
  if not ok then
    io.stderr:write("\n✗ Failed to run script '" .. script_name .. "': " .. tostring(result_or_err) .. "\n")
    return 1
  end

  local result = result_or_err
  if not result then
    io.stderr:write("\n✗ Script '" .. script_name .. "' returned empty result\n")
    return 1
  end

  if type(result) ~= "table" then
    io.stderr:write("\n✗ Unexpected result payload type: " .. type(result) .. "\n")
    return 1
  end

  local persist_ok, persist_err = persist_script_buffers(result, cfg, script_name)
  if not persist_ok then
    io.stderr:write("\n✗ Failed to persist script buffer: " .. tostring(persist_err) .. "\n")
    return 1
  end

  print("\n--- Reading Sensor Values (" .. script_name .. ") ---")
  for i, item in ipairs(result) do
    if script_name == "vibration" and type(item) == "table" and item.persist_buffer == true then
      goto continue
    end

    if type(item) == "table" then
      if item.object ~= nil or item.resource ~= nil then
        print(string.format(
          "[%d] object=%s instance=%s resource=%s value=%s",
          i,
          tostring(item.object),
          tostring(item.instance),
          tostring(item.resource),
          tostring(item.value)
        ))
      else
        local parts = {}
        local keys = table_keys_sorted(item)
        for _, key in ipairs(keys) do
          parts[#parts + 1] = key .. "=" .. tostring(item[key])
        end
        print(string.format("[%d] %s", i, table.concat(parts, " ")))
      end
    else
      print(string.format("[%d] value=%s", i, tostring(item)))
    end

    ::continue::
  end
  print("\n✓ Script '" .. script_name .. "' completed successfully")
  return 0
end

os.exit(main())
