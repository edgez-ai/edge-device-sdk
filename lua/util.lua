local function util_bytes_to_hex(bytes)
  return (bytes:gsub('.', function(c)
    return string.format('%02x', string.byte(c))
  end))
end

-- CRC-8 with polynomial 0x31 (x^8 + x^5 + x^4 + 1), init 0xFF
-- Used by SHT3x and other Sensirion sensors
local function util_crc8(data, poly, init)
  poly = poly or 0x31
  local crc = init or 0xFF
  for i = 1, #data do
    crc = crc ~ string.byte(data, i)
    for _ = 1, 8 do
      if (crc & 0x80) ~= 0 then
        crc = ((crc << 1) ~ poly) & 0xFF
      else
        crc = (crc << 1) & 0xFF
      end
    end
  end
  return crc
end

local function util_crc16_modbus(bytes)
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

local function util_build_read_holding_request(unit_id, address, count)
  local frame = string.char(
    unit_id & 0xFF,
    0x03,
    (address >> 8) & 0xFF,
    address & 0xFF,
    (count >> 8) & 0xFF,
    count & 0xFF
  )
  local crc = util_crc16_modbus(frame)
  return frame .. string.char(crc & 0xFF, (crc >> 8) & 0xFF)
end

local function util_extract_modbus_frame(buffer, unit_id, function_code, byte_count)
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
      local crc = util_crc16_modbus(frame:sub(1, -3))
      local lo = string.byte(frame, -2)
      local hi = string.byte(frame, -1)
      if lo == (crc & 0xFF) and hi == ((crc >> 8) & 0xFF) then
        return frame
      end
    end
  end
  return nil
end

local function util_decode_bcd_32(value)
  return ((value >> 28) & 0xF) * 10000000
    + ((value >> 24) & 0xF) * 1000000
    + ((value >> 20) & 0xF) * 100000
    + ((value >> 16) & 0xF) * 10000
    + ((value >> 12) & 0xF) * 1000
    + ((value >> 8) & 0xF) * 100
    + ((value >> 4) & 0xF) * 10
    + (value & 0xF)
end

local function util_log(cfg, tag, msg)
  if cfg and cfg.quiet then
    return
  end
  local prefix = tag or "Log"
  io.stderr:write("[" .. prefix .. "] " .. tostring(msg or "") .. "\n")
end

local native_util_get_sample_runtime = _G.util_get_sample_runtime

local function util_get_sample_runtime()
  if type(native_util_get_sample_runtime) == "function" then
    local ok, runtime = pcall(native_util_get_sample_runtime)
    if ok and type(runtime) == "table" then
      local sample_counter = tonumber(runtime.sample_counter) or 0
      local server_sec_of_year = tonumber(runtime.server_sec_of_year) or 0
      local server_sec_of_year_valid = runtime.server_sec_of_year_valid == true
      return {
        sample_counter = sample_counter,
        server_sec_of_year = server_sec_of_year,
        server_sec_of_year_valid = server_sec_of_year_valid,
      }
    end
  end

  return {
    sample_counter = 0,
    server_sec_of_year = 0,
    server_sec_of_year_valid = false,
  }
end

local function util_get_sample_counter()
  local runtime = util_get_sample_runtime()
  return tonumber(runtime.sample_counter) or 0
end

local function util_get_server_sec_of_year()
  local runtime = util_get_sample_runtime()
  return tonumber(runtime.server_sec_of_year) or 0, runtime.server_sec_of_year_valid == true
end

local function util_get_day_of_week()
  local sec_of_week, valid = util_get_server_sec_of_year()
  if not valid then
    return 0, false
  end
  return math.floor((tonumber(sec_of_week) or 0) % 604800 / 86400), true
end

local function util_get_hour_of_day()
  local sec_of_week, valid = util_get_server_sec_of_year()
  if not valid then
    return 0, false
  end
  local sec_of_day = (tonumber(sec_of_week) or 0) % 86400
  return math.floor(sec_of_day / 3600), true
end

local function util_get_min_of_day()
  local sec_of_week, valid = util_get_server_sec_of_year()
  if not valid then
    return 0, false
  end
  local sec_of_day = (tonumber(sec_of_week) or 0) % 86400
  return math.floor(sec_of_day / 60), true
end

local function util_sleep(seconds)
  local delay = tonumber(seconds) or 0
  if delay <= 0 then
    return true
  end

  if type(rs485_sleep) == "function" then
    rs485_sleep(delay)
    return true
  end

  if type(uart_sleep) == "function" then
    uart_sleep(delay)
    return true
  end

  if type(i2c_sleep) == "function" then
    i2c_sleep(delay)
    return true
  end

  local deadline = os.clock() + delay
  while os.clock() < deadline do
  end
  return true
end

local util_global_buffer = { chunks = {}, size = 0 }

local function util_init_global_buffer()
  util_global_buffer = { chunks = {}, size = 0 }
  return true
end

local function util_append_global_buffer(data)
  local buf = util_global_buffer

  local chunk = data
  if type(chunk) ~= "string" then
    chunk = tostring(chunk or "")
  end

  if #chunk > 0 then
    buf.chunks[#buf.chunks + 1] = chunk
    buf.size = buf.size + #chunk
  end

  return buf.size
end

local function util_get_global_buffer(clear_after_read)
  local data = table.concat(util_global_buffer.chunks)
  if clear_after_read then
    util_global_buffer = { chunks = {}, size = 0 }
  end
  return data
end

local function util_global_buffer_size()
  return util_global_buffer.size or 0
end

local function util_clear_global_buffer()
  util_global_buffer = { chunks = {}, size = 0 }
  return true
end

_G.util_bytes_to_hex = util_bytes_to_hex
_G.util_crc8 = util_crc8
_G.util_crc16_modbus = util_crc16_modbus
_G.util_build_read_holding_request = util_build_read_holding_request
_G.util_extract_modbus_frame = util_extract_modbus_frame
_G.util_decode_bcd_32 = util_decode_bcd_32
_G.util_log = util_log
_G.util_get_sample_runtime = util_get_sample_runtime
_G.util_get_sample_counter = util_get_sample_counter
_G.util_get_server_sec_of_year = util_get_server_sec_of_year
_G.util_get_day_of_week = util_get_day_of_week
_G.util_get_hour_of_day = util_get_hour_of_day
_G.util_get_min_of_day = util_get_min_of_day
_G.day_of_week = util_get_day_of_week
_G.hour_of_day = util_get_hour_of_day
_G.min_of_day = util_get_min_of_day
_G.util_sleep = util_sleep
_G.util_init_global_buffer = util_init_global_buffer
_G.util_append_global_buffer = util_append_global_buffer
_G.util_get_global_buffer = util_get_global_buffer
_G.util_global_buffer_size = util_global_buffer_size
_G.util_clear_global_buffer = util_clear_global_buffer

return {
  bytes_to_hex = util_bytes_to_hex,
  crc8 = util_crc8,
  crc16_modbus = util_crc16_modbus,
  build_read_holding_request = util_build_read_holding_request,
  extract_modbus_frame = util_extract_modbus_frame,
  decode_bcd_32 = util_decode_bcd_32,
  log = util_log,
  get_sample_runtime = util_get_sample_runtime,
  get_sample_counter = util_get_sample_counter,
  get_server_sec_of_year = util_get_server_sec_of_year,
  get_day_of_week = util_get_day_of_week,
  get_hour_of_day = util_get_hour_of_day,
  get_min_of_day = util_get_min_of_day,
  day_of_week = util_get_day_of_week,
  hour_of_day = util_get_hour_of_day,
  min_of_day = util_get_min_of_day,
  sleep = util_sleep,
  init_global_buffer = util_init_global_buffer,
  append_global_buffer = util_append_global_buffer,
  get_global_buffer = util_get_global_buffer,
  global_buffer_size = util_global_buffer_size,
  clear_global_buffer = util_clear_global_buffer,
}
