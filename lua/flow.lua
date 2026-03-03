local Flow = {}

local function read_holding_registers(cfg, address, count)
  local request = _G.util_build_read_holding_request(cfg.unit_id, address, count)
  _G.util_log(cfg, "Flow", "TX Read Holding Registers: " .. _G.util_bytes_to_hex(request))

  local ok, err = _G.rs485_reset_rx_cursor()
  if not ok then
    return nil, "failed to reset rx cursor: " .. tostring(err)
  end

  ok, err = _G.rs485_write(request)
  if not ok then
    return nil, "failed to write tx payload: " .. tostring(err)
  end

  local byte_count = count * 2
  local deadline = os.clock() + cfg.modbus_timeout
  local buffer = ""

  while os.clock() < deadline do
    local chunk = _G.rs485_read_chunk()
    if chunk and #chunk > 0 then
      buffer = buffer .. chunk
      local frame = _G.util_extract_modbus_frame(buffer, cfg.unit_id, 0x03, byte_count)
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
    if type(_G.rs485_sleep) == "function" then
      _G.rs485_sleep(0.02)
    end
  end

  return nil, "No valid Modbus response frame received"
end

function Flow.read_values(cfg)
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

  local flow_rate = _G.util_decode_bcd_32(flow_rate_raw) / cfg.flow_scale
  local total_volume = _G.util_decode_bcd_32(total_volume_raw) / cfg.volume_scale
  return {
    flow_rate = flow_rate,
    total_volume = total_volume,
    regs = regs,
  }
end

function Flow.run(cfg)
  if type(cfg) ~= "table" then
    return nil, "cfg must be a table"
  end

  if type(_G.rs485_write) ~= "function" or type(_G.rs485_read_chunk) ~= "function" then
    return nil, "rs485 global functions are not ready (load rs485_interface.lua first)"
  end

  if type(_G.util_build_read_holding_request) ~= "function"
    or type(_G.util_extract_modbus_frame) ~= "function"
    or type(_G.util_decode_bcd_32) ~= "function"
    or type(_G.util_bytes_to_hex) ~= "function"
    or type(_G.util_log) ~= "function" then
    return nil, "utility global functions are not ready (load util.lua first)"
  end

  if type(_G.rs485_connect) ~= "function" then
    return nil, "global function rs485_connect is not defined"
  end

  local ok, err = _G.rs485_connect(cfg.baud)
  if not ok then
    _G.rs485_safe_close()
    return nil, "failed to open rs485: " .. tostring(err)
  end

  local result, read_err = Flow.read_values(cfg)
  _G.rs485_safe_close()
  if not result then
    return nil, read_err
  end
  return result
end

return Flow
