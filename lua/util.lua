local function util_bytes_to_hex(bytes)
  return (bytes:gsub('.', function(c)
    return string.format('%02x', string.byte(c))
  end))
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

_G.util_bytes_to_hex = util_bytes_to_hex
_G.util_crc16_modbus = util_crc16_modbus
_G.util_build_read_holding_request = util_build_read_holding_request
_G.util_extract_modbus_frame = util_extract_modbus_frame
_G.util_decode_bcd_32 = util_decode_bcd_32
_G.util_log = util_log

return {
  bytes_to_hex = util_bytes_to_hex,
  crc16_modbus = util_crc16_modbus,
  build_read_holding_request = util_build_read_holding_request,
  extract_modbus_frame = util_extract_modbus_frame,
  decode_bcd_32 = util_decode_bcd_32,
  log = util_log,
}
