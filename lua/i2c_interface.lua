local I2C_OBJECT_ID = 10251
local I2C_RESOURCES = {
  type = 0,
  enabled = 1,
  open_state = 2,
  tx_bytes = 3,
  rx_bytes = 4,
  error_count = 5,
  last_error = 6,
  i2c_address = 7,
  mode = 8,
  reset_counters = 9,
  stats_window_ms = 10,
  tx_rate = 11,
  rx_rate = 12,
  tx_payload = 13,
  rx_buffer_pos = 14,
  rx_chunk = 15,
  rx_buffer_size = 16,
  tx_pin = 17,
  rx_pin = 18,
}

local function shell_quote(value)
  local s = tostring(value or "")
  s = s:gsub("'", "'\\''")
  return "'" .. s .. "'"
end

local function read_all(path, mode)
  local f = io.open(path, mode or "rb")
  if not f then
    return nil
  end
  local data = f:read("*a")
  f:close()
  return data
end

local function run_capture(cmd)
  local tmp = os.tmpname()
  local full = cmd .. " > " .. shell_quote(tmp) .. " 2>&1"
  local ok, _, code = os.execute(full)
  local out = read_all(tmp, "rb") or ""
  os.remove(tmp)
  if ok == true or code == 0 then
    return true, out
  end
  return false, out
end

local function base64_decode(input)
  local b = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/'
  input = input:gsub('[^' .. b .. '=]', '')
  return (input:gsub('.', function(x)
    if x == '=' then
      return ''
    end
    local r, f = '', (b:find(x, 1, true) or 1) - 1
    for i = 6, 1, -1 do
      r = r .. ((f % 2 ^ i - f % 2 ^ (i - 1) > 0) and '1' or '0')
    end
    return r
  end):gsub('%d%d%d?%d?%d?%d?%d?%d?', function(x)
    if #x ~= 8 then
      return ''
    end
    local c = 0
    for i = 1, 8 do
      if x:sub(i, i) == '1' then
        c = c + 2 ^ (8 - i)
      end
    end
    return string.char(c)
  end))
end

local function json_unescape(s)
  s = s:gsub('\\"', '"')
  s = s:gsub('\\/', '/')
  s = s:gsub('\\n', '\n')
  s = s:gsub('\\r', '\r')
  s = s:gsub('\\t', '\t')
  s = s:gsub('\\\\', '\\')
  return s
end

local function decode_response_payload(raw)
  if not raw or #raw == 0 then
    return ""
  end
  local first = raw:sub(1, 1)
  if first ~= "{" and first ~= "[" then
    return raw
  end

  local vd = raw:match('"vd"%s*:%s*"([^"]*)"')
  if vd then
    return base64_decode(json_unescape(vd))
  end

  local data = raw:match('"data"%s*:%s*"([^"]*)"')
  if data then
    return base64_decode(json_unescape(data))
  end

  local value = raw:match('"value"%s*:%s*"([^"]*)"')
  if value then
    local v = json_unescape(value)
    local decoded = base64_decode(v)
    if #decoded > 0 then
      return decoded
    end
    return v
  end

  local content = raw:match('"content"%s*:%s*"([^"]*)"')
  if content then
    local v = json_unescape(content)
    local decoded = base64_decode(v)
    if #decoded > 0 then
      return decoded
    end
    return v
  end

  return ""
end

local function busy_sleep(seconds)
  local wait = tonumber(seconds) or 0
  if wait <= 0 then
    return
  end
  local deadline = os.clock() + wait
  while os.clock() < deadline do
  end
end

-- REST backend for I2C over LwM2M
local RestBackend = {}
RestBackend.__index = RestBackend

function RestBackend:new(cfg)
  local obj = {
    cfg = {
      base_url = cfg.base_url,
      client = cfg.client,
      instance = cfg.instance or 0,
      http_timeout = cfg.http_timeout or 5.0,
    },
  }
  return setmetatable(obj, RestBackend)
end

function RestBackend:build_resource_url(res_id)
  return string.format(
    "%s/api/clients/%s/%d/%d/%d",
    tostring(self.cfg.base_url or ""):gsub("/+$", ""),
    tostring(self.cfg.client or ""),
    I2C_OBJECT_ID,
    tonumber(self.cfg.instance) or 0,
    res_id
  )
end

function RestBackend:put_text(res_id, value)
  local url = self:build_resource_url(res_id)
  local cmd = string.format(
    "curl -fsS -m %s -X PUT -H %s --data-binary %s %s",
    tostring(self.cfg.http_timeout),
    shell_quote("Content-Type: text/plain"),
    shell_quote(tostring(value)),
    shell_quote(url)
  )
  local ok, out = run_capture(cmd)
  if not ok then
    return false, out
  end
  return true
end

function RestBackend:put_bytes(res_id, payload)
  local url = self:build_resource_url(res_id)
  local tmp = os.tmpname()
  local f = io.open(tmp, "wb")
  if not f then
    return false, "failed to create temp file"
  end
  f:write(payload)
  f:close()

  local cmd = string.format(
    "curl -fsS -m %s -X PUT -H %s --data-binary @%s %s",
    tostring(self.cfg.http_timeout),
    shell_quote("Content-Type: application/octet-stream"),
    shell_quote(tmp),
    shell_quote(url)
  )
  local ok, out = run_capture(cmd)
  os.remove(tmp)
  if not ok then
    return false, out
  end
  return true
end

function RestBackend:get_payload(res_id)
  local url = self:build_resource_url(res_id)
  local cmd = string.format(
    "curl -fsS -m %s -H %s %s",
    tostring(self.cfg.http_timeout),
    shell_quote("Accept: application/octet-stream"),
    shell_quote(url)
  )
  local ok, out = run_capture(cmd)
  if not ok then
    return false, out
  end
  return true, decode_response_payload(out)
end

function RestBackend:configure(params)
  -- Close first
  local ok, err = self:put_text(I2C_RESOURCES.open_state, "false")
  if not ok then return false, err end
  busy_sleep(0.1)

  -- Set I2C address
  ok, err = self:put_text(I2C_RESOURCES.i2c_address, params.i2c_address)
  if not ok then return false, err end

  -- Set RX buffer size
  ok, err = self:put_text(I2C_RESOURCES.rx_buffer_size, params.rx_size)
  if not ok then return false, err end

  -- Set pins if provided
  if params.tx_pin ~= nil then
    ok, err = self:put_text(I2C_RESOURCES.tx_pin, params.tx_pin)
    if not ok then return false, err end
  end
  if params.rx_pin ~= nil then
    ok, err = self:put_text(I2C_RESOURCES.rx_pin, params.rx_pin)
    if not ok then return false, err end
  end

  return true
end

function RestBackend:open()
  return self:put_text(I2C_RESOURCES.open_state, "true")
end

function RestBackend:close()
  return self:put_text(I2C_RESOURCES.open_state, "false")
end

function RestBackend:reset_rx_cursor()
  return self:put_text(I2C_RESOURCES.rx_buffer_pos, 0)
end

function RestBackend:set_rx_size(size)
  return self:put_text(I2C_RESOURCES.rx_buffer_size, size)
end

function RestBackend:write(payload)
  return self:put_bytes(I2C_RESOURCES.tx_payload, payload)
end

function RestBackend:read_chunk()
  local ok, out = self:get_payload(I2C_RESOURCES.rx_chunk)
  if not ok then
    return ""
  end
  return out or ""
end

function RestBackend:sleep(seconds)
  busy_sleep(seconds)
end

-- Module-level functions

local Module = {}

local function new_native_backend(cfg)
  local native = rawget(_G, "i2c")
  if type(native) ~= "table" then
    return nil, nil
  end
  if type(native.new) == "function" then
    return native.new(cfg or {}), "native-object"
  end
  if type(native.open) == "function" and type(native.write) == "function" then
    return native, "native-global"
  end
  return nil, nil
end

function Module.new(cfg)
  cfg = cfg or {}
  local backend = cfg.backend

  if backend == "native" then
    local native = new_native_backend(cfg)
    if native then
      return native
    end
    error("native i2c backend requested but not available")
  end

  if backend ~= "rest" then
    local native = new_native_backend(cfg)
    if native then
      return native
    end
  end

  return RestBackend:new(cfg)
end

local active_backend = nil
local active_backend_kind = nil

local function current_cfg()
  return {
    backend = rawget(_G, "I2C_BACKEND") or "auto",
    base_url = rawget(_G, "I2C_BASE_URL"),
    client = rawget(_G, "I2C_CLIENT"),
    instance = rawget(_G, "I2C_INSTANCE") or 0,
    i2c_address = rawget(_G, "I2C_ADDRESS") or 0x44,
    tx_pin = rawget(_G, "I2C_TX_PIN"),
    rx_pin = rawget(_G, "I2C_RX_PIN"),
    rx_size = rawget(_G, "I2C_RX_SIZE") or 16,
    http_timeout = rawget(_G, "I2C_HTTP_TIMEOUT") or 5.0,
  }
end

local function create_backend(cfg)
  if cfg.backend == "native" then
    local backend, kind = new_native_backend(cfg)
    if not backend then
      return nil, nil, "native i2c backend requested but not available"
    end
    return backend, kind, nil
  end

  if cfg.backend ~= "rest" then
    local backend, kind = new_native_backend(cfg)
    if backend then
      return backend, kind, nil
    end
  end

  if not cfg.base_url or cfg.base_url == "" or not cfg.client or cfg.client == "" then
    return nil, nil, "I2C_BASE_URL and I2C_CLIENT are required for REST backend"
  end

  return RestBackend:new(cfg), "rest", nil
end

function _G.i2c_init()
  local cfg = current_cfg()
  local backend, kind, create_err = create_backend(cfg)
  if not backend then
    return false, create_err
  end

  local ok, err
  if kind == "native-global" then
    if type(backend.init) == "function" then
      ok, err = backend.init(cfg)
    elseif type(backend.configure) == "function" then
      ok, err = backend.configure(cfg)
    else
      ok = true
    end
  else
    ok, err = backend:configure(cfg)
  end

  if not ok then
    active_backend = nil
    active_backend_kind = nil
    return false, err
  end

  active_backend = backend
  active_backend_kind = kind
  return true
end

local function ensure_backend()
  if not active_backend then
    return nil, "i2c_init() must be called first"
  end
  return active_backend, nil
end

function _G.i2c_open()
  local backend, err = ensure_backend()
  if not backend then return false, err end

  if active_backend_kind == "native-global" and type(backend.open) == "function" then
    return backend.open()
  end
  return backend:open()
end

function _G.i2c_close()
  local backend, err = ensure_backend()
  if not backend then return false, err end

  if active_backend_kind == "native-global" and type(backend.close) == "function" then
    return backend.close()
  end
  return backend:close()
end

function _G.i2c_reset_rx_cursor()
  local backend, err = ensure_backend()
  if not backend then return false, err end

  if active_backend_kind == "native-global" and type(backend.reset_rx_cursor) == "function" then
    return backend.reset_rx_cursor()
  end
  return backend:reset_rx_cursor()
end

function _G.i2c_set_rx_size(size)
  local backend, err = ensure_backend()
  if not backend then return false, err end

  if active_backend_kind == "native-global" and type(backend.set_rx_size) == "function" then
    return backend.set_rx_size(size)
  end
  return backend:set_rx_size(size)
end

function _G.i2c_write(payload)
  local backend, err = ensure_backend()
  if not backend then return false, err end

  if active_backend_kind == "native-global" and type(backend.write) == "function" then
    return backend.write(payload)
  end
  return backend:write(payload)
end

function _G.i2c_read_chunk()
  local backend = active_backend
  if not backend then return "" end

  if active_backend_kind == "native-global" and type(backend.read_chunk) == "function" then
    local ok, out = pcall(backend.read_chunk)
    if not ok then return "" end
    return out or ""
  end
  local out = backend:read_chunk()
  return out or ""
end

function _G.i2c_sleep(seconds)
  local backend = active_backend
  if backend and active_backend_kind ~= "native-global" and type(backend.sleep) == "function" then
    return backend:sleep(seconds)
  end
  busy_sleep(seconds)
end

function _G.i2c_connect(i2c_address)
  if type(_G.i2c_init) ~= "function" then
    return false, "global function i2c_init is not defined"
  end
  if type(_G.i2c_open) ~= "function" then
    return false, "global function i2c_open is not defined"
  end
  if type(_G.i2c_reset_rx_cursor) ~= "function" then
    return false, "global function i2c_reset_rx_cursor is not defined"
  end

  -- Allow overriding address at connect time
  if i2c_address then
    _G.I2C_ADDRESS = i2c_address
  end

  local ok, err = _G.i2c_init()
  if not ok then return false, err end

  ok, err = _G.i2c_open()
  if not ok then return false, err end

  ok, err = _G.i2c_reset_rx_cursor()
  if not ok then return false, err end

  return true
end

function _G.i2c_safe_close()
  if type(_G.i2c_close) == "function" then
    _G.i2c_close()
  end
end

return Module
