local result = {}
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
local LWM2M_OBJECT = tonumber(rawget(_G, "VIBRATION_LWM2M_OBJECT")) or 3300
local LWM2M_VALUE_RESOURCE = tonumber(rawget(_G, "VIBRATION_LWM2M_VALUE_RESOURCE")) or 5700
local LWM2M_INSTANCE_BASE = tonumber(rawget(_G, "VIBRATION_INSTANCE_BASE")) or 0
local TEMP_LWM2M_OBJECT = tonumber(rawget(_G, "VIBRATION_TEMP_LWM2M_OBJECT")) or 3303
local TEMP_LWM2M_VALUE_RESOURCE = tonumber(rawget(_G, "VIBRATION_TEMP_LWM2M_VALUE_RESOURCE")) or 5700
local TEMP_LWM2M_INSTANCE = tonumber(rawget(_G, "VIBRATION_TEMP_INSTANCE")) or 0
local FULL_BLOCK_START = 0x34
local FULL_BLOCK_END = 0x9A
local FULL_BLOCK_COUNT = FULL_BLOCK_END - FULL_BLOCK_START + 1
local SAMPLE_HZ = 5
local SAMPLE_SECONDS = 10
local SAMPLE_INTERVAL = 1.0 / SAMPLE_HZ
local SAMPLE_COUNT = SAMPLE_HZ * SAMPLE_SECONDS
local RAW_OUTPUT = tostring(rawget(_G, "VIBRATION_RAW_OUTPUT") or "vibration_raw_samples.txt")
local GROUPS = {
  accel = { address = 0x34, count = 3 },
  velocity = { address = 0x3A, count = 3 },
  temp = { address = 0x40, count = 1 },
}
local OUTPUT_GROUPS = { "accel", "velocity", "temp" }
local function log(msg)
  util_log({ quiet = cfg.quiet }, "Vibration", msg)
end
local function sleep_seconds(seconds)
  if type(util_sleep) == "function" then
    util_sleep(seconds)
    return
  end
  if seconds == nil or seconds <= 0 then
    return
  end
  local deadline = os.clock() + seconds
  while os.clock() < deadline do
  end
end
local function to_signed16(v)
  if (v & 0x8000) ~= 0 then
    return v - 0x10000
  end
  return v
end
local metric_instances = {}
local next_instance = LWM2M_INSTANCE_BASE
local function metric_instance(metric)
  local id = metric_instances[metric]
  if id == nil then
    id = next_instance
    metric_instances[metric] = id
    next_instance = next_instance + 1
  end
  return id
end
local function add_lwm2m_metric(metric, value)
  local instance = metric_instance(metric)
  table.insert(result, {
    object = LWM2M_OBJECT,
    instance = instance,
    resource = LWM2M_VALUE_RESOURCE,
    value = value,
  })
end
local function add_temp_lwm2m_metric(value)
  table.insert(result, {
    object = TEMP_LWM2M_OBJECT,
    instance = TEMP_LWM2M_INSTANCE,
    resource = TEMP_LWM2M_VALUE_RESOURCE,
    value = value,
  })
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
    sleep_seconds(0.02)
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
  end
  return {}
end

local function decode_full_block(block)
  local merged_values = {}
  for _, name in ipairs(OUTPUT_GROUPS) do
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
  end
  return merged_values
end
local function append_raw_sample_to_global_buffer(_, regs)
  if type(util_append_global_buffer) ~= "function" then
    return nil, "util global buffer append helper is not available"
  end
  local parts = {}
  for i, reg in ipairs(regs) do
    parts[#parts + 1] = tostring(reg)
  end
  local line = table.concat(parts, ",") .. "\n"
  local ok, err = util_append_global_buffer(line)
  if not ok then
    return nil, err
  end
  return true
end
local function run_full_block_average()
  local block, err = read_holding_registers(FULL_BLOCK_START, FULL_BLOCK_COUNT)
  if not block then
    error("full-block read failed: " .. tostring(err))
  end
  local sums = {}
  local counts = {}
  if type(util_init_global_buffer) ~= "function" then
    error("util global buffer init helper is not available")
  end
  local init_ok, init_err = util_init_global_buffer()
  if not init_ok then
    error("failed to init global buffer: " .. tostring(init_err))
  end
  local function add_sample(sample_index, regs)
    local ok, append_err = append_raw_sample_to_global_buffer(sample_index, regs)
    if not ok then
      error("failed to append sample " .. tostring(sample_index) .. " to global buffer: " .. tostring(append_err))
    end
  end
  local first_values = decode_full_block(block)
  add_sample(1, block)
  for key, value in pairs(first_values) do
    if type(value) == "number" then
      sums[key] = (sums[key] or 0) + value
      counts[key] = (counts[key] or 0) + 1
    end
  end
  for sample = 2, SAMPLE_COUNT do
    local cycle_start = os.clock()
    local regs, read_err = read_holding_registers(FULL_BLOCK_START, FULL_BLOCK_COUNT)
    if not regs then
      error("full-block read failed at sample " .. tostring(sample) .. ": " .. tostring(read_err))
    end
    local values = decode_full_block(regs)
    add_sample(sample, regs)
    for key, value in pairs(values) do
      if type(value) == "number" then
        sums[key] = (sums[key] or 0) + value
        counts[key] = (counts[key] or 0) + 1
      end
    end

    local elapsed = os.clock() - cycle_start
    local sleep_time = SAMPLE_INTERVAL - elapsed
    if sleep_time > 0 then
      sleep_seconds(sleep_time)
    end
  end
  local keys = {}
  for key in pairs(sums) do
    keys[#keys + 1] = key
  end
  table.sort(keys)
  for _, key in ipairs(keys) do
    local avg = sums[key] / counts[key]
    if key == "temp_c" then
      add_temp_lwm2m_metric(avg)
    else
      add_lwm2m_metric("avg." .. key, avg)
    end
  end

  local buffered_bytes = nil
  if type(util_global_buffer_size) == "function" then
    buffered_bytes = util_global_buffer_size()
  end
  table.insert(result, {
    action = "full-block-average",
    status = "ok",
    message = "raw samples buffered",
    output = RAW_OUTPUT,
    bytes = buffered_bytes,
    persist_buffer = true,
  })
end
local ok, err = rs485_connect(cfg.baud)
if not ok then
  rs485_safe_close()
  error("failed to open rs485: " .. tostring(err))
end
run_full_block_average()
rs485_safe_close()
return result
