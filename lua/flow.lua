local Flow = {}

local function log(cfg, msg)
  if not cfg.quiet then
    io.stderr:write("[Flow] " .. msg .. "\n")
  end
end

local function connect_rs485(cfg)
  if type(_G.util_build_read_holding_request) ~= "function"
    or type(_G.util_extract_modbus_frame) ~= "function"
    or type(_G.util_decode_bcd_32) ~= "function"
    or type(_G.util_bytes_to_hex) ~= "function" then
    return false, "utility global functions are not ready (load util.lua first)"
  end

  if type(_G.rs485_init) ~= "function" then
    return false, "global function rs485_init is not defined"
  end

  local ok, err = _G.rs485_init(cfg.baud)
  if not ok then return false, err end

  if type(_G.rs485_open) ~= "function" then
    return false, "global function rs485_open is not defined"
  end
  ok, err = _G.rs485_open()
  if not ok then return false, err end

  if type(_G.rs485_reset_rx_cursor) ~= "function" then
    return false, "global function rs485_reset_rx_cursor is not defined"
  end
  ok, err = _G.rs485_reset_rx_cursor()
  if not ok then return false, err end

  return true
end

local function close_rs485()
  if type(_G.rs485_close) == "function" then
    _G.rs485_close()
  end
end

local function read_holding_registers(cfg, address, count)
  local request = _G.util_build_read_holding_request(cfg.unit_id, address, count)
  log(cfg, "TX Read Holding Registers: " .. _G.util_bytes_to_hex(request))

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

  local ok, err = connect_rs485(cfg)
  if not ok then
    close_rs485()
    return nil, "failed to open rs485: " .. tostring(err)
  end

  local result, read_err = Flow.read_values(cfg)
  close_rs485()
  if not result then
    return nil, read_err
  end
  return result
end

return Flow
