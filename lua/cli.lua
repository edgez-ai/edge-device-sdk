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
  flow_scale = 100000.0,
  volume_scale = 10000.0,
  i2c_address = 0x44,
  i2c_rx_size = 16,
  quiet = false,
  http_timeout = 5.0,
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
  print("  --i2c-address <ADDR>      I2C device address as decimal (default: 68 = 0x44)")
  print("  --help, -h                Show this help")
  print("")
  print("Available scripts:")
  print("  flow          Flow meter via RS485/Modbus")
  print("  sht3x_temp    SHT3x temperature & humidity via I2C")
  print("")
  print("Examples:")
  print("  lua lua/cli.lua --client B43A45A45A08 --base-url http://192.168.10.105:8088")
  print("  lua lua/cli.lua -c B43A45A45A08 -u http://192.168.10.105:8088 -s sht3x_temp")
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
  _G.RS485_RX_SIZE = cfg.rx_size
  _G.RS485_MODE = cfg.rs485_mode
  _G.RS485_UNIT_ID = cfg.unit_id
  _G.RS485_TX_PIN = cfg.tx_pin
  _G.RS485_RX_PIN = cfg.rx_pin
  _G.RS485_HTTP_TIMEOUT = cfg.http_timeout

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

  print("\n--- Reading Sensor Values (" .. script_name .. ") ---")
  for i, item in ipairs(result) do
    if type(item) == "table" then
      print(string.format(
        "[%d] object=%s instance=%s resource=%s value=%s",
        i,
        tostring(item.object),
        tostring(item.instance),
        tostring(item.resource),
        tostring(item.value)
      ))
    else
      print(string.format("[%d] value=%s", i, tostring(item)))
    end
  end
  print("\n✓ Script '" .. script_name .. "' completed successfully")
  return 0
end

os.exit(main())
