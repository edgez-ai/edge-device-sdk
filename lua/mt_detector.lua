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

local cam_baud = 921600
local cam_output =  "capture.jpg"
local cam_reset = false
local cam_quiet =  false

local log_cfg = { quiet = cam_quiet }



local function camera_log(msg)
  util_log(log_cfg, "VC0706", msg)
end

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

  if type(util_init_global_buffer) ~= "function" or type(util_append_global_buffer) ~= "function" then
    return nil, "util global buffer helpers are not available"
  end

  local init_ok, init_err = util_init_global_buffer()
  if not init_ok then
    return nil, "failed to init global buffer: " .. tostring(init_err)
  end

  local offset = 0

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
      return nil, "short read response at offset " .. tostring(offset) .. ": " .. tostring(#response) .. " bytes"
    end

    if not verify_response(response, CMD_READ_FBUF) then
      return nil, "invalid read-fbuf response header at offset " .. tostring(offset)
    end

    local payload_start = 6
    local payload_end = payload_start + chunk_size - 1
    if #response < payload_end then
      return nil, "chunk too short at offset " .. tostring(offset)
    end

    local payload = response:sub(payload_start, payload_end)
    local append_ok, append_err = util_append_global_buffer(payload)
    if not append_ok then
      return nil, "failed to append payload at offset " .. tostring(offset) .. ": " .. tostring(append_err)
    end

    payload = nil
    response = nil
    if collectgarbage then
      collectgarbage("step", 200)
    end

    offset = offset + chunk_size

    local progress = math.floor((offset * 100) / total)
    io.write(string.format("\rRead progress: %d%% (%d/%d)", progress, offset, total))
    io.flush()
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

    return true

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
local ok, err = uart_connect(cam_baud)
if not ok then
  error("failed to open rs485: " .. tostring(err))
end
ok, err = rs485_connect(cfg.baud)
if not ok then
  rs485_safe_close()
  error("failed to open rs485: " .. tostring(err))
end
uart_sleep(3.0)

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

result.output = cam_output
result.bytes = captured_len
result.persist_buffer = true

uart_safe_close()
rs485_safe_close()
return result
