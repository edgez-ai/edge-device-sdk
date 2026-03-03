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
local Util = require("util")
local Flow = require("flow")

local cfg_defaults = {
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
  quiet = false,
  http_timeout = 5.0,
}

local function parse_args(argv)
  local cfg = {}
  for k, v in pairs(cfg_defaults) do
    cfg[k] = v
  end

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
    elseif a == "--help" or a == "-h" then
      cfg.help = true
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
  print("  lua lua/cli.lua --client <ENDPOINT> --base-url <URL> [--backend rest|native|auto]")
  print("")
  print("Example:")
  print("  lua lua/cli.lua --client B43A45A45A08 --base-url http://192.168.10.105:8088")
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

  local _ = RS485
  local _u = Util

  local result, err = Flow.run(cfg)
  if not result then
    io.stderr:write("\n✗ Failed to read/decode flow meter values: " .. tostring(err) .. "\n")
    return 1
  end

  print("\n--- Reading Flow Meter Values (Injected RS485) ---")
  print(string.format("Flow: %.4f L/h", result.flow_rate))
  print(string.format("Volume: %.4f L", result.total_volume))
  print("\n✓ Flow meter read successful")
  return 0
end

os.exit(main())
