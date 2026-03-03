local result = {}

local util_build_read_holding_request = util_build_read_holding_request
local util_log = util_log
local util_bytes_to_hex = util_bytes_to_hex
local util_extract_modbus_frame = util_extract_modbus_frame
local util_decode_bcd_32 = util_decode_bcd_32

local rs485_connect = rs485_connect
local rs485_safe_close = rs485_safe_close
local rs485_reset_rx_cursor = rs485_reset_rx_cursor
local rs485_write = rs485_write
local rs485_read_chunk = rs485_read_chunk
local rs485_sleep = rs485_sleep

local BAUD = 9600
local UNIT_ID = 1
local ADDRESS = 0
local COUNT = 4
local MODBUS_TIMEOUT = 1.0
local FLOW_SCALE = 100000.0
local VOLUME_SCALE = 10000.0
local FLOW_OBJECT = 3345
local FLOW_RATE_RESOURCE = 5700
local TOTAL_VOLUME_RESOURCE = 5701

local LOG_CFG = { quiet = false }

local function read_holding_registers(address, count)
  local request = util_build_read_holding_request(UNIT_ID, address, count)
  util_log(LOG_CFG, "Flow", "TX Read Holding Registers: " .. util_bytes_to_hex(request))

  local ok, err = rs485_reset_rx_cursor()
  if not ok then
    return nil, "failed to reset rx cursor: " .. tostring(err)
  end

  ok, err = rs485_write(request)
  if not ok then
    return nil, "failed to write tx payload: " .. tostring(err)
  end

  local byte_count = count * 2
  local deadline = os.clock() + MODBUS_TIMEOUT
  local buffer = ""

  while os.clock() < deadline do
    local chunk = rs485_read_chunk()
    if chunk and #chunk > 0 then
      buffer = buffer .. chunk
      local frame = util_extract_modbus_frame(buffer, UNIT_ID, 0x03, byte_count)
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
    if type(rs485_sleep) == "function" then
      rs485_sleep(0.02)
    end
  end

  return nil, "No valid Modbus response frame received"
end

local function read_values()
  local count = COUNT == 5 and 4 or COUNT
  local regs, err = read_holding_registers(ADDRESS, count)
  if not regs then
    return nil, err
  end
  if #regs < 4 then
    return nil, "insufficient register count"
  end

  local flow_rate_raw = (regs[3] << 16) | regs[4]
  local total_volume_raw = (regs[1] << 16) | regs[2]

  local flow_rate = util_decode_bcd_32(flow_rate_raw) / FLOW_SCALE
  local total_volume = util_decode_bcd_32(total_volume_raw) / VOLUME_SCALE
  return {
    flow_rate = flow_rate,
    total_volume = total_volume,
    regs = regs,
  }
end

local ok, err = rs485_connect(BAUD)
if not ok then
  rs485_safe_close()
  error("failed to open rs485: " .. tostring(err))
end

local result, read_err = read_values()
rs485_safe_close()
if not result then
  error(read_err)
end


table.insert(result, {
  object = FLOW_OBJECT,
  instance = 0,
  resource = FLOW_RATE_RESOURCE,
  value = result.flow_rate,
})

table.insert(result, {
  object = FLOW_OBJECT,
  instance = 0,
  resource = TOTAL_VOLUME_RESOURCE,
  value = result.total_volume,
})

return result
