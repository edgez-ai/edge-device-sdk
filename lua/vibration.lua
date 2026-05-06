local result = {}

require("rs485_interface")
require("util")

local cfg = {
  baud = tonumber(rawget(_G, "RS485_BAUD")) or 9600,
  unit_id = tonumber(rawget(_G, "RS485_UNIT_ID")) or 0x50,
  rx_size = tonumber(rawget(_G, "RS485_RX_SIZE")) or 256,
  rs485_mode = tonumber(rawget(_G, "RS485_MODE")) or 0,
  modbus_timeout = tonumber(rawget(_G, "RS485_MODBUS_TIMEOUT")) or 1.0,
  group = tostring(rawget(_G, "VIBRATION_GROUP") or "basic"),
  action = tostring(rawget(_G, "VIBRATION_ACTION") or "read"),
  address = tonumber(rawget(_G, "RS485_ADDRESS")) or 0x34,
  count = tonumber(rawget(_G, "RS485_COUNT")) or 3,
  quiet = rawget(_G, "VIBRATION_QUIET") == true,
}

local FULL_BLOCK_START = 0x34
local FULL_BLOCK_END = 0x9A
local FULL_BLOCK_COUNT = FULL_BLOCK_END - FULL_BLOCK_START + 1

local GROUPS = {
  accel = { address = 0x34, count = 3 },
  velocity = { address = 0x3A, count = 3 },
  temp = { address = 0x40, count = 1 },
  displacement = { address = 0x41, count = 3 },
  frequency = { address = 0x44, count = 3 },
  ["x-advanced"] = { address = 0x47, count = 12 },
  ["y-advanced"] = { address = 0x53, count = 12 },
  ["z-advanced"] = { address = 0x5F, count = 12 },
}

local BASIC_GROUPS = { "accel", "velocity", "temp", "displacement", "frequency" }
local ALL_GROUPS = { "accel", "velocity", "temp", "displacement", "frequency", "x-advanced", "y-advanced", "z-advanced" }

local function log(msg)
  util_log({ quiet = cfg.quiet }, "Vibration", msg)
end

local function to_signed16(v)
  if (v & 0x8000) ~= 0 then
    return v - 0x10000
  end
  return v
end

local function fmt_num(v)
  return string.format("%.6f", v)
end

local function read_holding_registers(address, count)
  local request = util_build_read_holding_request(cfg.unit_id, address, count)
  log("TX Read Holding Registers: " .. util_bytes_to_hex(request))

  local ok, err = rs485_reset_rx_cursor()
  if not ok then
    return nil, "failed to reset rx cursor: " .. tostring(err)
  end

  ok, err = rs485_write(request)
  if not ok then
    return nil, "failed to write tx payload: " .. tostring(err)
  end

  local byte_count = count * 2
  local deadline = os.clock() + cfg.modbus_timeout
  local buffer = ""

  while os.clock() < deadline do
    local chunk = rs485_read_chunk()
    if chunk and #chunk > 0 then
      buffer = buffer .. chunk
      local frame = util_extract_modbus_frame(buffer, cfg.unit_id, 0x03, byte_count)
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
    util_sleep(0.02)
  end

  return nil, "no valid Modbus response frame received"
end

local function decode_group(name, regs)
  if name == "accel" then
    return {
      ax_g = to_signed16(regs[1]) / 32768.0 * 16.0,
      ay_g = to_signed16(regs[2]) / 32768.0 * 16.0,
      az_g = to_signed16(regs[3]) / 32768.0 * 16.0,
    }
  elseif name == "velocity" then
    return {
      vx_mm_s = regs[1] / 100.0,
      vy_mm_s = regs[2] / 100.0,
      vz_mm_s = regs[3] / 100.0,
    }
  elseif name == "temp" then
    return { temp_c = to_signed16(regs[1]) / 100.0 }
  elseif name == "displacement" then
    return {
      dx_um = regs[1],
      dy_um = regs[2],
      dz_um = regs[3],
      dx_mm = regs[1] / 1000.0,
      dy_mm = regs[2] / 1000.0,
      dz_mm = regs[3] / 1000.0,
    }
  elseif name == "frequency" then
    return {
      hzx_hz = regs[1] / 10.0,
      hzy_hz = regs[2] / 10.0,
      hzz_hz = regs[3] / 10.0,
    }
  elseif name == "x-advanced" then
    return {
      cfx = regs[1] / 1000.0,
      kx = regs[2] / 1000.0,
      aavgx = regs[3] / 1000.0,
      varx = regs[4] / 1000.0,
      rrax = regs[5] / 1000.0,
      wix = regs[6] / 1000.0,
      pix = regs[7] / 1000.0,
      pcx = regs[8] / 1000.0,
      skx = regs[9] / 1000.0,
      vrmsx_mm_s = regs[10] / 1000.0,
      vkx = regs[11] / 1000.0,
      drmsx_mm = regs[12] / 1000.0,
    }
  elseif name == "y-advanced" then
    return {
      cfy = regs[1] / 1000.0,
      ky = regs[2] / 1000.0,
      aavgy = regs[3] / 1000.0,
      vary = regs[4] / 1000.0,
      rray = regs[5] / 1000.0,
      wiy = regs[6] / 1000.0,
      piy = regs[7] / 1000.0,
      pcy = regs[8] / 1000.0,
      sky = regs[9] / 1000.0,
      vrmsy_mm_s = regs[10] / 1000.0,
      vky = regs[11] / 1000.0,
      drmsy_mm = regs[12] / 1000.0,
    }
  elseif name == "z-advanced" then
    return {
      cfz = regs[1] / 1000.0,
      kz = regs[2] / 1000.0,
      aavgz = regs[3] / 1000.0,
      varz = regs[4] / 1000.0,
      rraz = regs[5] / 1000.0,
      wiz = regs[6] / 1000.0,
      piz = regs[7] / 1000.0,
      pcz = regs[8] / 1000.0,
      skz = regs[9] / 1000.0,
      vrmsz_mm_s = regs[10] / 1000.0,
      vkz = regs[11] / 1000.0,
      drmsz_mm = regs[12] / 1000.0,
    }
  end
  return {}
end

local function estimate_vibration(values)
  local vx = tonumber(values.vx_mm_s)
  local vy = tonumber(values.vy_mm_s)
  local vz = tonumber(values.vz_mm_s)
  if not vx or not vy or not vz then
    return "unknown"
  end
  local vmax = math.max(vx, vy, vz)
  if vmax >= 2.0 then
    return string.format("strong vibration (max=%.3f mm/s)", vmax)
  elseif vmax >= 0.5 then
    return string.format("moderate vibration (max=%.3f mm/s)", vmax)
  elseif vmax >= 0.1 then
    return string.format("light vibration (max=%.3f mm/s)", vmax)
  end
  return string.format("nearly still (max=%.3f mm/s)", vmax)
end

local function estimate_posture(values)
  local ax = tonumber(values.ax_g)
  local ay = tonumber(values.ay_g)
  local az = tonumber(values.az_g)
  if not ax or not ay or not az then
    return "unknown"
  end

  local norm = math.sqrt(ax * ax + ay * ay + az * az)
  if norm < 0.6 or norm > 1.4 then
    return string.format("dynamic motion (|a|=%.2fg)", norm)
  end

  local absx, absy, absz = math.abs(ax), math.abs(ay), math.abs(az)
  if absz >= absx and absz >= absy then
    if az >= 0 then
      return string.format("+Z up (|a|=%.2fg)", norm)
    end
    return string.format("-Z up (inverted, |a|=%.2fg)", norm)
  end
  if absx >= absy then
    if ax >= 0 then
      return string.format("+X up (|a|=%.2fg)", norm)
    end
    return string.format("-X up (|a|=%.2fg)", norm)
  end
  if ay >= 0 then
    return string.format("+Y up (|a|=%.2fg)", norm)
  end
  return string.format("-Y up (|a|=%.2fg)", norm)
end

local function add_group_rows(group_name, regs, values)
  table.insert(result, {
    group = group_name,
    address = GROUPS[group_name].address,
    count = GROUPS[group_name].count,
    registers = table.concat(regs, ","),
  })
  local keys = {}
  for k in pairs(values) do
    keys[#keys + 1] = k
  end
  table.sort(keys)
  for _, key in ipairs(keys) do
    local value = values[key]
    if type(value) == "number" then
      value = fmt_num(value)
    end
    table.insert(result, {
      group = group_name,
      metric = key,
      value = value,
    })
  end
end

local function run_read_groups(group_names)
  local merged_values = {}
  for _, name in ipairs(group_names) do
    local g = GROUPS[name]
    local regs, err = read_holding_registers(g.address, g.count)
    if not regs then
      error("group " .. name .. " read failed: " .. tostring(err))
    end
    local values = decode_group(name, regs)
    for k, v in pairs(values) do
      merged_values[k] = v
    end
    add_group_rows(name, regs, values)
  end

  table.insert(result, { summary = "vibration", value = estimate_vibration(merged_values) })
  table.insert(result, { summary = "posture", value = estimate_posture(merged_values) })
end

local function run_full_block_once()
  local block, err = read_holding_registers(FULL_BLOCK_START, FULL_BLOCK_COUNT)
  if not block then
    error("full-block read failed: " .. tostring(err))
  end

  local merged_values = {}
  for _, name in ipairs(ALL_GROUPS) do
    local g = GROUPS[name]
    local offset = g.address - FULL_BLOCK_START
    local regs = {}
    for i = 1, g.count do
      regs[i] = block[offset + i]
    end
    local values = decode_group(name, regs)
    for k, v in pairs(values) do
      merged_values[k] = v
    end
    add_group_rows(name, regs, values)
  end

  table.insert(result, {
    summary = "read_mode",
    value = string.format("single-request (%d regs from 0x%X)", FULL_BLOCK_COUNT, FULL_BLOCK_START),
  })
  table.insert(result, { summary = "vibration", value = estimate_vibration(merged_values) })
  table.insert(result, { summary = "posture", value = estimate_posture(merged_values) })
end

local function run_raw()
  local regs, err = read_holding_registers(cfg.address, cfg.count)
  if not regs then
    error("raw read failed: " .. tostring(err))
  end

  table.insert(result, {
    action = "raw",
    address = cfg.address,
    count = cfg.count,
    registers = table.concat(regs, ","),
  })
end

local ok, err = rs485_connect(cfg.baud)
if not ok then
  rs485_safe_close()
  error("failed to open rs485: " .. tostring(err))
end

if cfg.action == "raw" then
  run_raw()
elseif cfg.group == "full-block" then
  run_full_block_once()
elseif cfg.group == "all" then
  run_read_groups(ALL_GROUPS)
elseif cfg.group == "basic" then
  run_read_groups(BASIC_GROUPS)
else
  if not GROUPS[cfg.group] then
    rs485_safe_close()
    error("unsupported group: " .. tostring(cfg.group))
  end
  run_read_groups({ cfg.group })
end

rs485_safe_close()
return result
