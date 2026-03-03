local result = {}

local rs485_baud = 9600
local rs485_unit_id = 1
local rs485_address = 0
local rs485_count = 4
local rs485_modbus_timeout = 1.0
local rs485_flow_scale = 100000.0
local rs485_volume_scale = 10000.0
local rs485_flow_object = 3345
local rs485_flow_rate_resource = 5700
local rs485_total_volume_resource = 5701

local rs485_log_cfg = { quiet = false }

local function rs485_read_holding_registers(rs485_read_address, rs485_read_count)
  local rs485_request = util_build_read_holding_request(rs485_unit_id, rs485_read_address, rs485_read_count)
  util_log(rs485_log_cfg, "Flow", "TX Read Holding Registers: " .. util_bytes_to_hex(rs485_request))

  local rs485_ok, rs485_err = rs485_reset_rx_cursor()
  if not rs485_ok then
    return nil, "failed to reset rx cursor: " .. tostring(rs485_err)
  end

  rs485_ok, rs485_err = rs485_write(rs485_request)
  if not rs485_ok then
    return nil, "failed to write tx payload: " .. tostring(rs485_err)
  end

  local rs485_byte_count = rs485_read_count * 2
  local rs485_deadline = os.clock() + rs485_modbus_timeout
  local rs485_buffer = ""

  while os.clock() < rs485_deadline do
    local rs485_chunk = rs485_read_chunk()
    if rs485_chunk and #rs485_chunk > 0 then
      rs485_buffer = rs485_buffer .. rs485_chunk
      local rs485_frame = util_extract_modbus_frame(rs485_buffer, rs485_unit_id, 0x03, rs485_byte_count)
      if rs485_frame then
        local rs485_payload = rs485_frame:sub(4, -3)
        local rs485_regs = {}
        for rs485_i = 1, #rs485_payload, 2 do
          local rs485_hi = string.byte(rs485_payload, rs485_i)
          local rs485_lo = string.byte(rs485_payload, rs485_i + 1)
          rs485_regs[#rs485_regs + 1] = (rs485_hi << 8) | rs485_lo
        end
        return rs485_regs
      end
    end
    if type(rs485_sleep) == "function" then
      rs485_sleep(0.02)
    end
  end

  return nil, "No valid Modbus response frame received"
end

local function rs485_read_values()
  local rs485_effective_count = rs485_count == 5 and 4 or rs485_count
  local rs485_regs, rs485_err = rs485_read_holding_registers(rs485_address, rs485_effective_count)
  if not rs485_regs then
    return nil, rs485_err
  end
  if #rs485_regs < 4 then
    return nil, "insufficient register count"
  end

  local rs485_flow_rate_raw = (rs485_regs[3] << 16) | rs485_regs[4]
  local rs485_total_volume_raw = (rs485_regs[1] << 16) | rs485_regs[2]

  local rs485_flow_rate = util_decode_bcd_32(rs485_flow_rate_raw) / rs485_flow_scale
  local rs485_total_volume = util_decode_bcd_32(rs485_total_volume_raw) / rs485_volume_scale
  return {
    flow_rate = rs485_flow_rate,
    total_volume = rs485_total_volume,
    regs = rs485_regs,
  }
end

local rs485_ok, rs485_err = rs485_connect(rs485_baud)
if not rs485_ok then
  rs485_safe_close()
  error("failed to open rs485: " .. tostring(rs485_err))
end

local rs485_result, rs485_read_err = rs485_read_values()
rs485_safe_close()
if not rs485_result then
  error(rs485_read_err)
end


table.insert(result, {
  object = rs485_flow_object,
  instance = 0,
  resource = rs485_flow_rate_resource,
  value = rs485_result.flow_rate,
})

table.insert(result, {
  object = rs485_flow_object,
  instance = 0,
  resource = rs485_total_volume_resource,
  value = rs485_result.total_volume,
})

return result
