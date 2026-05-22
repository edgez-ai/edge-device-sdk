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

local SERIAL_NUM = 0x00

local CMD_RESET = 0x26
local CMD_SET_DOWNSIZE = 0x31
local CMD_FBUF_CTRL = 0x36
local CMD_GET_FBUF_LEN = 0x34
local CMD_READ_FBUF = 0x32

local FBUF_STOP_FRAME = 0x00
local FBUF_RESUME_FRAME = 0x02

local READ_CHUNK_SIZE = 250
local MAX_FRAME_SIZE = 500000
local FULL_BLOCK_START = 0x34
local FULL_BLOCK_END = 0x40
local FULL_BLOCK_COUNT = FULL_BLOCK_END - FULL_BLOCK_START + 1
local IMAGE_END_MARKER = "\r\n\r\n"

local LWM2M_OBJECT = tonumber(rawget(_G, "VIBRATION_LWM2M_OBJECT")) or 3300
local LWM2M_VALUE_RESOURCE = tonumber(rawget(_G, "VIBRATION_LWM2M_VALUE_RESOURCE")) or 5700
local LWM2M_INSTANCE_BASE = tonumber(rawget(_G, "VIBRATION_INSTANCE_BASE")) or 0
local TEMP_LWM2M_OBJECT = tonumber(rawget(_G, "VIBRATION_TEMP_LWM2M_OBJECT")) or 3303
local TEMP_LWM2M_VALUE_RESOURCE = tonumber(rawget(_G, "VIBRATION_TEMP_LWM2M_VALUE_RESOURCE")) or 5700
local TEMP_LWM2M_INSTANCE = tonumber(rawget(_G, "VIBRATION_TEMP_INSTANCE")) or 0

local GROUPS = {
  accel    = { address = 0x34, count = 3 },
  velocity = { address = 0x3A, count = 3 },
  temp     = { address = 0x40, count = 1 },
}
local OUTPUT_GROUPS = { "accel", "velocity", "temp" }

local cam_baud = 921600
local cam_output =  "capture.jpg"
local cam_reset = false
local cam_quiet =  false

local log_cfg = { quiet = cam_quiet }



local function camera_log(msg)
  util_log(log_cfg, "VC0706", msg)
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
  local merged = {}
  for _, name in ipairs(OUTPUT_GROUPS) do
    local g = GROUPS[name]
    local offset = g.address - FULL_BLOCK_START
    local regs = {}
    for i = 1, g.count do
      regs[i] = block[offset + i]
    end
    local values = decode_group(name, regs)
    for k, v in pairs(values) do
      merged[k] = v
    end
  end
  return merged
end

local sums = {}
local counts = {}

local function build_command(cmd, args)
  args = args or ""
  return string.char(0x56, SERIAL_NUM, cmd & 0xFF, #args & 0xFF) .. args
end

local function send_command(cmd, args, label)
  local packet = build_command(cmd, args)

  local ok, err = uart_reset_rx_cursor()
  if not ok then
    return false, "failed to reset rx cursor: " .. tostring(err)
  end

  ok, err = uart_write(packet)
  if not ok then
    return false, "failed to write command payload: " .. tostring(err)
  end

  return true
end

local function read_response(timeout_seconds, max_len, expected_len)
  local timeout = tonumber(timeout_seconds) or 2.0
  local max_bytes = tonumber(max_len) or 256
  local wanted = expected_len and tonumber(expected_len) or nil
  if wanted and wanted > max_bytes then
    wanted = max_bytes
  end

  local data = ""
  local no_data_count = 0
  local deadline = os.clock() + timeout

  while #data < max_bytes and os.clock() < deadline do
    local chunk = uart_read_chunk()
    if chunk and #chunk > 0 then
      data = data .. chunk
      no_data_count = 0
      if wanted and #data >= wanted then
        break
      end
    else
      no_data_count = no_data_count + 1
      if #data > 0 and no_data_count >= 30 then
        break
      end
      uart_sleep(0.05)
    end
  end

  if #data > max_bytes then
    data = data:sub(1, max_bytes)
  end

  return data
end

local function drain_buffer(timeout_seconds)
  local timeout = tonumber(timeout_seconds) or 0.3
  local drained = 0
  local deadline = os.clock() + timeout
  while os.clock() < deadline do
    local chunk = uart_read_chunk()
    if chunk and #chunk > 0 then
      drained = drained + #chunk
    else
      uart_sleep(0.05)
    end
  end
  if drained > 0 then
    camera_log("Drained " .. tostring(drained) .. " stray bytes")
  end
  return drained
end

local function verify_response(response, cmd)
  if not response or #response < 4 then
    return false
  end
  return string.byte(response, 1) == 0x76
    and string.byte(response, 2) == SERIAL_NUM
    and string.byte(response, 3) == (cmd & 0xFF)
    and string.byte(response, 4) == 0x00
end

local function read_holding_registers(address, count)
  local request = util_build_read_holding_request(cfg.unit_id, address, count)
  local ok, err = rs485_reset_rx_cursor()
  if not ok then
    return nil, "failed to reset rs485 rx cursor: " .. tostring(err)
  end

  ok, err = rs485_write(request)
  if not ok then
    return nil, "failed to write rs485 request: " .. tostring(err)
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
  end

  return nil, "no valid modbus response frame received"
end

local CSV_COLUMNS = { "ax_g", "ay_g", "az_g", "vx_mm_s", "vy_mm_s", "vz_mm_s", "temp_c" }
local CSV_HEADER = table.concat(CSV_COLUMNS, ",") .. "\n"

local function build_decoded_csv_line(values)
  local parts = {}
  for _, col in ipairs(CSV_COLUMNS) do
    parts[#parts + 1] = string.format("%.4f", values[col] or 0)
  end
  return table.concat(parts, ",") .. "\n"
end

  local get_frame_buffer_length
  local resume_frame

local function stop_frame_and_get_length(max_attempts)
  local attempts = tonumber(max_attempts) or 3
  local stop_ok = false
  local stop_err = "failed to stop frame"

  for attempt = 1, attempts do
    local ok, err = send_command(CMD_FBUF_CTRL, string.char(FBUF_STOP_FRAME), "STOP_FRAME")
    if ok then
      local response = read_response(2.0, 64)
      if #response >= 5 and verify_response(response, CMD_FBUF_CTRL) then
        stop_ok = true
        break
      end
    else
      stop_err = err
    end

    camera_log(string.format("Retry STOP_FRAME attempt %d/%d", attempt, attempts))
    drain_buffer(0.1)
  end

  if not stop_ok then
    return nil, stop_err
  end

  local frame_len = 0
  for _ = 1, attempts do
    drain_buffer(0.1)
    frame_len = get_frame_buffer_length()
    if frame_len > 0 then
      return frame_len
    end
  end

  resume_frame()
  return nil, "failed to get frame length"
end

resume_frame = function()
  local ok, err = send_command(CMD_FBUF_CTRL, string.char(FBUF_RESUME_FRAME), "RESUME_FRAME")
  if not ok then
    return false, err
  end

  local response = read_response(1.0, 64)
  if #response >= 5 and verify_response(response, CMD_FBUF_CTRL) then
    return true
  end
  return #response > 0, "failed to resume frame"
end

get_frame_buffer_length = function()
  local ok, err = send_command(CMD_GET_FBUF_LEN, string.char(0x00), "GET_FBUF_LEN")
  if not ok then
    return 0, err
  end

  local response = read_response(2.0, 64)
  if #response >= 9 and verify_response(response, CMD_GET_FBUF_LEN) then
    local b6 = string.byte(response, 6)
    local b7 = string.byte(response, 7)
    local b8 = string.byte(response, 8)
    local b9 = string.byte(response, 9)
    local length = ((b6 << 24) | (b7 << 16) | (b8 << 8) | b9)
    camera_log("Frame buffer length: " .. tostring(length) .. " bytes")
    return length
  end

  return 0, "invalid frame length response"
end

local function build_read_fbuf_args(offset, chunk_size)
  return string.char(
    0x00, 0x0A,
    (offset >> 24) & 0xFF,
    (offset >> 16) & 0xFF,
    (offset >> 8) & 0xFF,
    offset & 0xFF,
    (chunk_size >> 24) & 0xFF,
    (chunk_size >> 16) & 0xFF,
    (chunk_size >> 8) & 0xFF,
    chunk_size & 0xFF,
    0x00,
    0xFF
  )
end

local function read_frame_buffer_to_global(length, max_retries)
  local total = tonumber(length) or 0
  local retries = tonumber(max_retries) or 3

  if total <= 0 or total > MAX_FRAME_SIZE then
    return nil, "invalid frame length: " .. tostring(total)
  end

  if type(util_init_global_buffer) ~= "function"
    or type(util_write_global_buffer_at) ~= "function" then
    return nil, "util global buffer helpers are not available"
  end

  local init_ok, init_err = util_init_global_buffer()
  if not init_ok then
    return nil, "failed to init global buffer: " .. tostring(init_err)
  end

  local marker_ok, marker_err = util_write_global_buffer_at(total, IMAGE_END_MARKER)
  if not marker_ok then
    return nil, "failed to write image marker: " .. tostring(marker_err)
  end

  local offset = 0
  local csv_write_pos = total + #IMAGE_END_MARKER

  local write_hdr_ok, write_hdr_err = util_write_global_buffer_at(csv_write_pos, CSV_HEADER)
  if not write_hdr_ok then
    return nil, "failed to write csv header: " .. tostring(write_hdr_err)
  end
  csv_write_pos = csv_write_pos + #CSV_HEADER

  while offset < total do
    local chunk_size = math.min(READ_CHUNK_SIZE, total - offset)
    local args = build_read_fbuf_args(offset, chunk_size)

    local response = ""
    for attempt = 1, retries do
      local ok = send_command(CMD_READ_FBUF, args, string.format("READ_FBUF@%d#%d", offset, attempt))
      if ok then
        response = read_response(4.0, chunk_size + 10, chunk_size + 10)
        if #response >= (5 + chunk_size) and verify_response(response, CMD_READ_FBUF) then
          break
        end
      end
      camera_log(string.format("Retry chunk offset %d attempt %d/%d, got %d bytes", offset, attempt, retries, #response))
    end

    if #response < 10 then
      camera_log("short read response at offset " .. tostring(offset) .. ": " .. tostring(#response) .. " bytes, writing dummy")
      util_write_global_buffer_at(offset, string.rep("\0", chunk_size))
    elseif not verify_response(response, CMD_READ_FBUF) then
      camera_log("invalid read-fbuf response header at offset " .. tostring(offset) .. ", writing dummy")
      util_write_global_buffer_at(offset, string.rep("\0", chunk_size))
    else
      local payload_start = 6
      local payload_end = payload_start + chunk_size - 1
      if #response < payload_end then
        camera_log("chunk too short at offset " .. tostring(offset) .. ", writing dummy")
        util_write_global_buffer_at(offset, string.rep("\0", chunk_size))
      else
        local payload = response:sub(payload_start, payload_end)
        local write_img_ok, write_img_err = util_write_global_buffer_at(offset, payload)
        if not write_img_ok then
          return nil, "failed to write image payload at offset " .. tostring(offset) .. ": " .. tostring(write_img_err)
        end
      end
    end

    local regs, regs_err = read_holding_registers(FULL_BLOCK_START, FULL_BLOCK_COUNT)
    if not regs then
      camera_log("RS485 full-block read failed at image offset " .. tostring(offset) .. ": " .. tostring(regs_err) .. "; continue")
    else
      local values = decode_full_block(regs)
      local csv_line = build_decoded_csv_line(values)
      local write_csv_ok, write_csv_err = util_write_global_buffer_at(csv_write_pos, csv_line)
      if not write_csv_ok then
        return nil, "failed to write sensor csv line at position " .. tostring(csv_write_pos) .. ": " .. tostring(write_csv_err)
      end
      csv_write_pos = csv_write_pos + #csv_line
      for key, value in pairs(values) do
        if type(value) == "number" then
          sums[key] = (sums[key] or 0) + value
          counts[key] = (counts[key] or 0) + 1
        end
      end
    end

    payload = nil
    response = nil
    if collectgarbage then
      collectgarbage("step", 200)
    end

    offset = offset + chunk_size


  end
  print("")
  return true
end

local function reset_camera()
  local ok, err = send_command(CMD_RESET, "", "RESET")
  if not ok then
    return false, err
  end
  local response = read_response(3.0, 64)
  if #response >= 4 then
    return true
  end
  return false, "no reset response"
end

local function set_resolution()

  drain_buffer(0.2)
  local packet = string.char(0x56, 0x00, 0x31, 0x05, 0x05, 0x01, 0x00, 0x19, 0x33)
  local ok, err = uart_reset_rx_cursor()
  if not ok then
    return false, "failed to reset rx cursor: " .. tostring(err)
  end
  ok, err = uart_write(packet)
  if not ok then
    return false, err
  end

  local response = read_response(2.0, 64)
  if #response >= 5 then
    local ack = response:sub(1, 5)
    local expected = string.char(0x76, SERIAL_NUM, CMD_SET_DOWNSIZE, 0x01, 0x00)
    if ack == expected then
      return true
    end
  end

  if #response >= 4 and verify_response(response, CMD_SET_DOWNSIZE) then
    return true
  end

  return false, "resolution command not acknowledged"

end

local function capture_image(frame_len)
  local total = tonumber(frame_len) or 0
  if total <= 0 then
    return nil, "invalid frame length"
  end

  local ok, read_err = read_frame_buffer_to_global(total, 3)
  resume_frame()
  if not ok then
    return nil, read_err
  end
  return total
end


-- Main execution starts here
uart_safe_close()
rs485_safe_close()
uart_sleep(1.5)
local ok, err = uart_connect(cam_baud)
if not ok then
  error("failed to open rs485: " .. tostring(err))
end

uart_sleep(1)
ok, err = rs485_connect(cfg.baud)
uart_sleep(2)
if not ok then
  rs485_safe_close()
  error("failed to open rs485: " .. tostring(err))
end

if cam_reset then
  local reset_ok, reset_err = reset_camera()
  if not reset_ok then
    uart_safe_close()
    rs485_safe_close()
    error("camera reset failed: " .. tostring(reset_err))
  end
end

local set_ok, set_err = set_resolution()
if not set_ok then
  uart_safe_close()
  rs485_safe_close()
  error("failed to set resolution before capture: " .. tostring(set_err))
end

local frame_len, frame_err = stop_frame_and_get_length(3)
if not frame_len then
  uart_safe_close()
  rs485_safe_close()
  error("capture failed: " .. tostring(frame_err))
end

local captured_len, cap_err = capture_image(frame_len)
if not captured_len then
  uart_safe_close()
  rs485_safe_close()  
  error("capture failed: " .. tostring(cap_err))
end

local avg_keys = {}
for key in pairs(sums) do avg_keys[#avg_keys + 1] = key end
table.sort(avg_keys)
for _, key in ipairs(avg_keys) do
  local avg = sums[key] / counts[key]
  if key == "temp_c" then
    add_temp_lwm2m_metric(avg)
  else
    add_lwm2m_metric("avg." .. key, avg)
  end
end

result.output = cam_output
result.bytes = captured_len
result.persist_buffer = true

uart_safe_close()
rs485_safe_close()
return result
