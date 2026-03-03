#!/usr/bin/env lua

local function add_script_path()
  local script = (arg and arg[0]) or ""
  local dir = script:match("^(.*)/[^/]+$")
  if dir and dir ~= "" then
    package.path = dir .. "/?.lua;" .. package.path
  end
end

local function load_rs485_module()
  local ok, mod = pcall(require, "rs485_interface")
  if ok and type(mod) == "table" and type(mod.new) == "function" then
    return mod
  end
  error("failed to load rs485_interface.lua")
end

add_script_path()
local RS485 = load_rs485_module()

local function bytes_to_hex(bytes)
  return (bytes:gsub('.', function(c)
    return string.format('%02x', string.byte(c))
  end))
end

local function crc16_modbus(bytes)
  local crc = 0xFFFF
  for i = 1, #bytes do
    crc = crc ~ string.byte(bytes, i)
    for _ = 1, 8 do
      if (crc & 0x0001) ~= 0 then
        crc = ((crc >> 1) ~ 0xA001) & 0xFFFF
      else
        crc = (crc >> 1) & 0xFFFF
      end
    end
  end
  return crc
end

local function build_read_holding_request(unit_id, address, count)
  local frame = string.char(
    unit_id & 0xFF,
    0x03,
    (address >> 8) & 0xFF,
    address & 0xFF,
    (count >> 8) & 0xFF,
    count & 0xFF
  )
  local crc = crc16_modbus(frame)
  return frame .. string.char(crc & 0xFF, (crc >> 8) & 0xFF)
end

local function extract_frame(buffer, unit_id, function_code, byte_count)
  local expected_len = 5 + byte_count
  if #buffer < expected_len then
    return nil
  end
  for start = 1, #buffer - expected_len + 1 do
    local b1 = string.byte(buffer, start)
    local b2 = string.byte(buffer, start + 1)
    local b3 = string.byte(buffer, start + 2)
    if b1 == unit_id and b2 == function_code and b3 == byte_count then
      local frame = buffer:sub(start, start + expected_len - 1)
      local crc = crc16_modbus(frame:sub(1, -3))
      local lo = string.byte(frame, -2)
      local hi = string.byte(frame, -1)
      if lo == (crc & 0xFF) and hi == ((crc >> 8) & 0xFF) then
        return frame
      end
    end
  end
  return nil
end

local function decode_bcd_32(value)
  return ((value >> 28) & 0xF) * 10000000
    + ((value >> 24) & 0xF) * 1000000
    + ((value >> 20) & 0xF) * 100000
    + ((value >> 16) & 0xF) * 10000
    + ((value >> 12) & 0xF) * 1000
    + ((value >> 8) & 0xF) * 100
    + ((value >> 4) & 0xF) * 10
    + (value & 0xF)
end

local function parse_args(argv)
  local args = {
    backend = "auto",
    base_url = "http://192.168.10.177:8088",
    instance = 0,
    baud = 4800,
    unit_id = 1,
    address = 0,
    count = 4,
    rx_size = 256,
    rs485_mode = 0,
    modbus_timeout = 1.0,
    flow_scale = 100000.0,
    volume_scale = 10000.0,
    action = "flow",
    quiet = false,
  }

  local i = 1
  while i <= #argv do
    local a = argv[i]
    local function next_value()
      i = i + 1
      return argv[i]
    end

    if a == "--client" or a == "-c" then
      args.client = next_value()
    elseif a == "--backend" then
      args.backend = next_value()
    elseif a == "--base-url" or a == "-u" then
      args.base_url = next_value()
    elseif a == "--instance" or a == "-i" then
      args.instance = tonumber(next_value())
    elseif a == "--baud" or a == "-b" then
      args.baud = tonumber(next_value())
    elseif a == "--tx-pin" then
      args.tx_pin = tonumber(next_value())
    elseif a == "--rx-pin" then
      args.rx_pin = tonumber(next_value())
    elseif a == "--unit-id" then
      args.unit_id = tonumber(next_value())
    elseif a == "--address" then
      args.address = tonumber(next_value())
    elseif a == "--count" then
      args.count = tonumber(next_value())
    elseif a == "--rx-size" then
      args.rx_size = tonumber(next_value())
    elseif a == "--rs485-mode" then
      args.rs485_mode = tonumber(next_value())
    elseif a == "--modbus-timeout" then
      args.modbus_timeout = tonumber(next_value())
    elseif a == "--flow-scale" then
      args.flow_scale = tonumber(next_value())
    elseif a == "--volume-scale" then
      args.volume_scale = tonumber(next_value())
    elseif a == "--action" then
      args.action = next_value()
    elseif a == "--quiet" or a == "-q" then
      args.quiet = true
    elseif a == "--help" or a == "-h" then
      args.help = true
    else
      io.stderr:write("Unknown argument: " .. tostring(a) .. "\n")
      args.help = true
      args.invalid = true
    end
    i = i + 1
  end

  return args
end

local function print_usage()
  print("Usage:")
  print("  lua lua/modbus_rtu_rest.lua --client <ENDPOINT> --base-url <URL> [options]")
  print("")
  print("Compatible flow example:")
  print("  lua lua/modbus_rtu_rest.lua --client B43A45A45A08 --base-url http://192.168.10.105:8088 --action flow --baud 9600")
  print("")
  print("Options:")
  print("  --backend            auto|rest|native (default: auto)")
  print("  --client, -c         LwM2M endpoint (required for --backend rest)")
  print("  --base-url, -u       REST base URL (used by --backend rest)")
  print("  --action             Only 'flow' is supported in this Lua version")
  print("  --baud, -b           RS485 baud (default: 4800)")
  print("  --unit-id            Modbus unit ID (default: 1)")
  print("  --address            Register address (default: 0)")
  print("  --count              Register count (default: 4)")
  print("  --tx-pin / --rx-pin  Optional pin override")
  print("  --modbus-timeout     Modbus timeout seconds (default: 1.0)")
end

local function log(cfg, msg)
  if not cfg.quiet then
    io.stderr:write("[ModbusRTU] " .. msg .. "\n")
  end
end

local function connect_rs485(cfg, rs485)
  local ok, err = rs485:configure(cfg)
  if not ok then return false, err end

  ok, err = rs485:open()
  if not ok then return false, err end

  ok, err = rs485:reset_rx_cursor()
  if not ok then return false, err end

  return true
end

local function close_rs485(rs485)
  if rs485 and rs485.close then
    rs485:close()
  end
end

local function read_holding_registers(cfg, rs485, address, count)
  local request = build_read_holding_request(cfg.unit_id, address, count)
  log(cfg, "TX Read Holding Registers: " .. bytes_to_hex(request))

  local ok, err = rs485:reset_rx_cursor()
  if not ok then
    return nil, "failed to reset rx cursor: " .. tostring(err)
  end

  ok, err = rs485:write(request)
  if not ok then
    return nil, "failed to write tx payload: " .. tostring(err)
  end

  local byte_count = count * 2
  local deadline = os.clock() + cfg.modbus_timeout
  local buffer = ""

  while os.clock() < deadline do
    local chunk = rs485:read_chunk()
    if chunk and #chunk > 0 then
      buffer = buffer .. chunk
      local frame = extract_frame(buffer, cfg.unit_id, 0x03, byte_count)
      if frame then
        local payload = frame:sub(4, -3)
        local regs = {}
        for i = 1, #payload, 2 do
          local hi = string.byte(payload, i)
          local lo = string.byte(payload, i + 1)
          regs[#regs + 1] = (hi << 8) | lo
        end
        return regs
      end
    end
    if rs485.sleep then
      rs485:sleep(0.02)
    end
  end

  return nil, "No valid Modbus response frame received"
end

local function read_flow_values(cfg, rs485)
  local count = cfg.count == 5 and 4 or cfg.count
  local regs, err = read_holding_registers(cfg, rs485, cfg.address, count)
  if not regs then
    return nil, err
  end
  if #regs < 4 then
    return nil, "insufficient register count"
  end

  local flow_rate_raw = (regs[3] << 16) | regs[4]
  local total_volume_raw = (regs[1] << 16) | regs[2]

  local flow_rate = decode_bcd_32(flow_rate_raw) / cfg.flow_scale
  local total_volume = decode_bcd_32(total_volume_raw) / cfg.volume_scale
  return {
    flow_rate = flow_rate,
    total_volume = total_volume,
    regs = regs,
  }
end

local function main()
  local args = parse_args(arg)
  if args.help or args.invalid then
    print_usage()
    return args.invalid and 1 or 0
  end

  if args.backend == "rest" and (not args.client or args.client == "") then
    io.stderr:write("--client is required\n\n")
    print_usage()
    return 1
  end

  if args.action ~= "flow" then
    io.stderr:write("This Lua version currently supports only --action flow\n")
    return 1
  end

  local cfg = {
    backend = args.backend,
    client = args.client,
    base_url = args.base_url,
    instance = args.instance,
    baud = args.baud,
    tx_pin = args.tx_pin,
    rx_pin = args.rx_pin,
    unit_id = args.unit_id,
    address = args.address,
    count = args.count,
    rx_size = args.rx_size,
    rs485_mode = args.rs485_mode,
    modbus_timeout = args.modbus_timeout,
    flow_scale = args.flow_scale,
    volume_scale = args.volume_scale,
    quiet = args.quiet,
    http_timeout = math.max(5.0, (args.modbus_timeout or 1.0) + 4.0),
  }

  local rs485 = RS485.new(cfg)

  if cfg.client and cfg.client ~= "" then
    print(string.format("Connecting to %s as %s...", cfg.base_url, cfg.client))
  else
    print("Connecting to RS485 backend...")
  end

  local ok, err = connect_rs485(cfg, rs485)
  if not ok then
    io.stderr:write("Failed to open RS485: " .. tostring(err) .. "\n")
    close_rs485(rs485)
    return 1
  end

  local result, read_err = read_flow_values(cfg, rs485)
  close_rs485(rs485)

  if not result then
    io.stderr:write("\n✗ Failed to read/decode flow meter values: " .. tostring(read_err) .. "\n")
    return 1
  end

  print("\n--- Reading Flow Meter Values (Lua RS485 REST) ---")
  print(string.format("Flow: %.4f L/h", result.flow_rate))
  print(string.format("Volume: %.4f L", result.total_volume))
  print("\n✓ Flow meter read successful")
  return 0
end

os.exit(main())
