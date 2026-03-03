#!/usr/bin/env lua

local RS485_OBJECT_ID = 10252
local RS485_RESOURCES = {
  open_state = 2,
  baudrate = 7,
  modbus_unit_id = 8,
  mode = 9,
  tx_payload = 14,
  rx_buffer_pos = 15,
  rx_chunk = 16,
  rx_buffer_size = 17,
  tx_pin = 18,
  rx_pin = 19,
}

local function shell_quote(value)
  local s = tostring(value or "")
  s = s:gsub("'", "'\\''")
  return "'" .. s .. "'"
end

local function read_all(path, mode)
  local f = io.open(path, mode or "rb")
  if not f then
    return nil
  end
  local data = f:read("*a")
  f:close()
  return data
end

local function run_capture(cmd)
  local tmp = os.tmpname()
  local full = cmd .. " > " .. shell_quote(tmp) .. " 2>&1"
  local ok, _, code = os.execute(full)
  local out = read_all(tmp, "rb") or ""
  os.remove(tmp)
  if ok == true or code == 0 then
    return true, out
  end
  return false, out
end

local function sleep_s(seconds)
  os.execute(string.format("sleep %.3f", seconds))
end

local function base64_decode(input)
  local b = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
  input = input:gsub('[^' .. b .. '=]', '')
  return (input:gsub('.', function(x)
    if x == '=' then
      return ''
    end
    local r, f = '', (b:find(x, 1, true) or 1) - 1
    for i = 6, 1, -1 do
      r = r .. ((f % 2 ^ i - f % 2 ^ (i - 1) > 0) and '1' or '0')
    end
    return r
  end):gsub('%d%d%d?%d?%d?%d?%d?%d?', function(x)
    if #x ~= 8 then
      return ''
    end
    local c = 0
    for i = 1, 8 do
      if x:sub(i, i) == '1' then
        c = c + 2 ^ (8 - i)
      end
    end
    return string.char(c)
  end))
end

local function json_unescape(s)
  s = s:gsub('\\"', '"')
  s = s:gsub('\\/', '/')
  s = s:gsub('\\n', '\n')
  s = s:gsub('\\r', '\r')
  s = s:gsub('\\t', '\t')
  s = s:gsub('\\\\', '\\')
  return s
end

local function decode_response_payload(raw)
  if not raw or #raw == 0 then
    return ""
  end
  local first = raw:sub(1, 1)
  if first ~= "{" and first ~= "[" then
    return raw
  end

  local vd = raw:match('"vd"%s*:%s*"([^"]*)"')
  if vd then
    return base64_decode(json_unescape(vd))
  end

  local data = raw:match('"data"%s*:%s*"([^"]*)"')
  if data then
    return base64_decode(json_unescape(data))
  end

  local value = raw:match('"value"%s*:%s*"([^"]*)"')
  if value then
    local v = json_unescape(value)
    local decoded = base64_decode(v)
    if #decoded > 0 then
      return decoded
    end
    return v
  end

  local content = raw:match('"content"%s*:%s*"([^"]*)"')
  if content then
    local v = json_unescape(content)
    local decoded = base64_decode(v)
    if #decoded > 0 then
      return decoded
    end
    return v
  end

  return ""
end

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
  print("  --client, -c         LwM2M endpoint (required)")
  print("  --base-url, -u       REST base URL (default: http://192.168.10.177:8088)")
  print("  --action             Only 'flow' is supported in this Lua version")
  print("  --baud, -b           RS485 baud (default: 4800)")
  print("  --unit-id            Modbus unit ID (default: 1)")
  print("  --address            Register address (default: 0)")
  print("  --count              Register count (default: 4)")
  print("  --tx-pin / --rx-pin  Optional pin override")
  print("  --modbus-timeout     Modbus timeout seconds (default: 1.0)")
end

local function build_resource_url(cfg, res_id)
  return string.format(
    "%s/api/clients/%s/%d/%d/%d",
    cfg.base_url:gsub("/+$", ""),
    cfg.client,
    RS485_OBJECT_ID,
    cfg.instance,
    res_id
  )
end

local function curl_put_text(cfg, res_id, value)
  local url = build_resource_url(cfg, res_id)
  local cmd = string.format(
    "curl -fsS -m %s -X PUT -H %s --data-binary %s %s",
    tostring(cfg.http_timeout),
    shell_quote("Content-Type: text/plain"),
    shell_quote(tostring(value)),
    shell_quote(url)
  )
  local ok, out = run_capture(cmd)
  if not ok then
    return false, out
  end
  return true
end

local function curl_put_bytes(cfg, res_id, payload)
  local url = build_resource_url(cfg, res_id)
  local tmp = os.tmpname()
  local f = io.open(tmp, "wb")
  if not f then
    return false, "failed to create temp file"
  end
  f:write(payload)
  f:close()

  local cmd = string.format(
    "curl -fsS -m %s -X PUT -H %s --data-binary @%s %s",
    tostring(cfg.http_timeout),
    shell_quote("Content-Type: application/octet-stream"),
    shell_quote(tmp),
    shell_quote(url)
  )
  local ok, out = run_capture(cmd)
  os.remove(tmp)
  if not ok then
    return false, out
  end
  return true
end

local function curl_get(cfg, res_id, accept_octet)
  local url = build_resource_url(cfg, res_id)
  local header = accept_octet and ("-H " .. shell_quote("Accept: application/octet-stream")) or ""
  local cmd = string.format(
    "curl -fsS -m %s %s %s",
    tostring(cfg.http_timeout),
    header,
    shell_quote(url)
  )
  local ok, out = run_capture(cmd)
  if not ok then
    return false, out
  end
  return true, out
end

local function log(cfg, msg)
  if not cfg.quiet then
    io.stderr:write("[ModbusRTU] " .. msg .. "\n")
  end
end

local function write_res(cfg, name, value)
  local res = RS485_RESOURCES[name]
  if not res then
    return true
  end
  local ok, err = curl_put_text(cfg, res, value)
  if not ok then
    return false, err
  end
  sleep_s(0.05)
  return true
end

local function connect_rs485(cfg)
  local ok, err
  ok, err = write_res(cfg, "open_state", "false")
  if not ok then return false, err end
  sleep_s(0.1)

  if cfg.tx_pin ~= nil then
    ok, err = write_res(cfg, "tx_pin", cfg.tx_pin)
    if not ok then return false, err end
  end
  if cfg.rx_pin ~= nil then
    ok, err = write_res(cfg, "rx_pin", cfg.rx_pin)
    if not ok then return false, err end
  end

  ok, err = write_res(cfg, "baudrate", cfg.baud)
  if not ok then return false, err end

  ok, err = write_res(cfg, "rx_buffer_size", cfg.rx_size)
  if not ok then return false, err end

  ok, err = write_res(cfg, "modbus_unit_id", cfg.unit_id)
  if not ok then return false, err end

  ok, err = write_res(cfg, "mode", cfg.rs485_mode)
  if not ok then return false, err end

  ok, err = write_res(cfg, "open_state", "true")
  if not ok then return false, err end

  ok, err = write_res(cfg, "rx_buffer_pos", 0)
  if not ok then return false, err end

  return true
end

local function close_rs485(cfg)
  write_res(cfg, "open_state", "false")
end

local function read_rx_chunk(cfg)
  local ok, out = curl_get(cfg, RS485_RESOURCES.rx_chunk, true)
  if not ok then
    return ""
  end
  return decode_response_payload(out)
end

local function read_holding_registers(cfg, address, count)
  local request = build_read_holding_request(cfg.unit_id, address, count)
  log(cfg, "TX Read Holding Registers: " .. bytes_to_hex(request))

  local ok, err = write_res(cfg, "rx_buffer_pos", 0)
  if not ok then
    return nil, "failed to reset rx cursor: " .. tostring(err)
  end

  ok, err = curl_put_bytes(cfg, RS485_RESOURCES.tx_payload, request)
  if not ok then
    return nil, "failed to write tx payload: " .. tostring(err)
  end

  local byte_count = count * 2
  local deadline = os.clock() + cfg.modbus_timeout
  local buffer = ""

  while os.clock() < deadline do
    local chunk = read_rx_chunk(cfg)
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
    sleep_s(0.02)
  end

  return nil, "No valid Modbus response frame received"
end

local function read_flow_values(cfg)
  local count = cfg.count == 5 and 4 or cfg.count
  local regs, err = read_holding_registers(cfg, cfg.address, count)
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

  if not args.client or args.client == "" then
    io.stderr:write("--client is required\n\n")
    print_usage()
    return 1
  end

  if args.action ~= "flow" then
    io.stderr:write("This Lua version currently supports only --action flow\n")
    return 1
  end

  local cfg = {
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

  print(string.format("Connecting to %s as %s...", cfg.base_url, cfg.client))

  local ok, err = connect_rs485(cfg)
  if not ok then
    io.stderr:write("Failed to open RS485: " .. tostring(err) .. "\n")
    close_rs485(cfg)
    return 1
  end

  local result, read_err = read_flow_values(cfg)
  close_rs485(cfg)

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
