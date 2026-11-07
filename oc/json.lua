--[[ json.lua: minimal JSON for Gregtec OS's OpenComputers programs (WO-7.2).

Written for this repo (MIT, see LICENSE at the repo root). No third-party code. It compiles on
Lua 5.2 and 5.3, so a program that needs 5.3 can still load it and say so.

json.encode(value) -> string
  * ASCII only: every non-ASCII character is written as \uXXXX (a surrogate pair above U+FFFF)
    and every invalid UTF-8 byte as �, so the text survives any charset on its way. OC
    writes an HTTP request body with the JVM's default charset, which is not UTF-8 on every
    Java build.
  * Object keys are sorted, so one table always gives the same text.
  * Tables made with json.array() are arrays and json.object() objects. Any other table is an
    array when its keys are exactly 1..n (n >= 1), otherwise an object (string keys only).
  * NaN and infinities become null. A function, userdata, a non-string key or nesting deeper
    than 32 raises an error.

json.decode(text) -> value | nil, err
  * RFC 8259, strict: no comments, no trailing commas, no leading zeros, nothing after the value.
  * null decodes to json.null, so arrays keep their positions; json.isnull(v) is true for nil
    and json.null. Arrays and objects come back marked (json.is_array works on them).
  * Whole numbers that fit in 64 bits are Lua integers on 5.3; other numbers are floats.
  * Nesting deeper than 32 is an error. It never raises; errors are returned.
]]

local json = {}

local byte, char, format, concat, sort = string.byte, string.char, string.format, table.concat, table.sort
local floor, huge = math.floor, math.huge

local MAX_DEPTH = 32
local ARRAY_MT = { __name = "json.array" }
local OBJECT_MT = { __name = "json.object" }

json.null = setmetatable({}, {
  __name = "json.null",
  __tostring = function()
    return "null"
  end,
})

function json.array(t)
  return setmetatable(t or {}, ARRAY_MT)
end

function json.object(t)
  return setmetatable(t or {}, OBJECT_MT)
end

function json.is_array(t)
  return type(t) == "table" and getmetatable(t) == ARRAY_MT
end

function json.is_object(t)
  return type(t) == "table" and getmetatable(t) ~= ARRAY_MT and t ~= json.null
end

function json.isnull(v)
  return v == nil or v == json.null
end

-- Lua 5.2 has no math.type; there every number is a float.
local mtype = math.type
  or function(x)
    if x == floor(x) and x > -2 ^ 53 and x < 2 ^ 53 then
      return "integer"
    end
    return "float"
  end

---------------------------------------------------------------------------------------------
-- Encoding

local SHORT = {
  ['"'] = '\\"',
  ["\\"] = "\\\\",
  ["\b"] = "\\b",
  ["\f"] = "\\f",
  ["\n"] = "\\n",
  ["\r"] = "\\r",
  ["\t"] = "\\t",
}

-- Characters that need work: controls (and DEL), quote, backslash, every byte >= 0x80.
local SPECIAL = '[%c"\\\128-\255]'

-- The code point of the UTF-8 sequence that starts at s[i] (first byte c), and its length;
-- nil when the bytes there are not valid UTF-8 (overlong, surrogate, beyond U+10FFFF, cut short).
local function utf8_at(s, i, c)
  local c2, c3, c4 = byte(s, i + 1, i + 3)
  if c >= 0xC2 and c <= 0xDF then
    if c2 and c2 >= 0x80 and c2 <= 0xBF then
      return (c - 0xC0) * 64 + (c2 - 0x80), 2
    end
  elseif c >= 0xE0 and c <= 0xEF then
    local lo, hi = 0x80, 0xBF
    if c == 0xE0 then
      lo = 0xA0
    elseif c == 0xED then
      hi = 0x9F
    end
    if c2 and c2 >= lo and c2 <= hi and c3 and c3 >= 0x80 and c3 <= 0xBF then
      return ((c - 0xE0) * 64 + (c2 - 0x80)) * 64 + (c3 - 0x80), 3
    end
  elseif c >= 0xF0 and c <= 0xF4 then
    local lo, hi = 0x80, 0xBF
    if c == 0xF0 then
      lo = 0x90
    elseif c == 0xF4 then
      hi = 0x8F
    end
    if c2 and c2 >= lo and c2 <= hi and c3 and c3 >= 0x80 and c3 <= 0xBF and c4 and c4 >= 0x80 and c4 <= 0xBF then
      return (((c - 0xF0) * 64 + (c2 - 0x80)) * 64 + (c3 - 0x80)) * 64 + (c4 - 0x80), 4
    end
  end
  return nil
end

local function encode_string(s, out, n)
  if not s:find(SPECIAL) then
    n = n + 1
    out[n] = '"' .. s .. '"'
    return n
  end
  n = n + 1
  out[n] = '"'
  local i, len = 1, #s
  while i <= len do
    local j = s:find(SPECIAL, i)
    if not j then
      n = n + 1
      out[n] = s:sub(i)
      break
    end
    if j > i then
      n = n + 1
      out[n] = s:sub(i, j - 1)
    end
    local c = byte(s, j)
    if c < 0x80 then
      local ch = char(c)
      n = n + 1
      out[n] = SHORT[ch] or format("\\u%04x", c)
      i = j + 1
    else
      local cp, l = utf8_at(s, j, c)
      n = n + 1
      if not cp then
        out[n] = "\\ufffd"
        i = j + 1
      elseif cp >= 0x10000 then
        cp = cp - 0x10000
        out[n] = format("\\u%04x\\u%04x", 0xD800 + floor(cp / 1024), 0xDC00 + cp % 1024)
        i = j + l
      else
        out[n] = format("\\u%04x", cp)
        i = j + l
      end
    end
  end
  n = n + 1
  out[n] = '"'
  return n
end

local function encode_number(x)
  if mtype(x) == "integer" then
    return format("%d", x)
  end
  if x ~= x or x == huge or x == -huge then
    return "null"
  end
  local s = format("%.14g", x)
  if tonumber(s) ~= x then
    s = format("%.17g", x)
  end
  return s
end

-- An unmarked table is an array when its keys are exactly 1..n.
local function is_sequence(t)
  local n = #t
  if n == 0 then
    return false
  end
  local count = 0
  for k in pairs(t) do
    if type(k) ~= "number" or k < 1 or k > n or k % 1 ~= 0 then
      return false
    end
    count = count + 1
  end
  return count == n
end

local encode_value

local function encode_table(t, out, n, depth)
  if depth >= MAX_DEPTH then
    error("json: nesting deeper than " .. MAX_DEPTH, 0)
  end
  local mt = getmetatable(t)
  if mt == ARRAY_MT or (mt ~= OBJECT_MT and is_sequence(t)) then
    n = n + 1
    out[n] = "["
    for i = 1, #t do
      if i > 1 then
        n = n + 1
        out[n] = ","
      end
      n = encode_value(t[i], out, n, depth + 1)
    end
    n = n + 1
    out[n] = "]"
    return n
  end
  local keys, kn = {}, 0
  for k in pairs(t) do
    if type(k) ~= "string" then
      error("json: object key is a " .. type(k), 0)
    end
    kn = kn + 1
    keys[kn] = k
  end
  sort(keys)
  n = n + 1
  out[n] = "{"
  for i = 1, kn do
    if i > 1 then
      n = n + 1
      out[n] = ","
    end
    n = encode_string(keys[i], out, n)
    n = n + 1
    out[n] = ":"
    n = encode_value(t[keys[i]], out, n, depth + 1)
  end
  n = n + 1
  out[n] = "}"
  return n
end

encode_value = function(v, out, n, depth)
  local tv = type(v)
  if tv == "string" then
    return encode_string(v, out, n)
  elseif tv == "number" then
    n = n + 1
    out[n] = encode_number(v)
    return n
  elseif tv == "boolean" then
    n = n + 1
    out[n] = v and "true" or "false"
    return n
  elseif v == nil or v == json.null then
    n = n + 1
    out[n] = "null"
    return n
  elseif tv == "table" then
    return encode_table(v, out, n, depth)
  end
  error("json: cannot encode a " .. tv, 0)
end

function json.encode(v)
  local out = {}
  local n = encode_value(v, out, 0, 0)
  return concat(out, "", 1, n)
end

---------------------------------------------------------------------------------------------
-- Decoding

local function fail(pos, msg)
  error({ pos = pos, msg = msg }, 0)
end

local function skip(s, i)
  local _, e = s:find("^[ \t\r\n]*", i)
  return e + 1
end

local ESCAPES = { ['"'] = '"', ["\\"] = "\\", ["/"] = "/", b = "\b", f = "\f", n = "\n", r = "\r", t = "\t" }

local function utf8_char(cp)
  if cp < 0x80 then
    return char(cp)
  elseif cp < 0x800 then
    return char(0xC0 + floor(cp / 64), 0x80 + cp % 64)
  elseif cp < 0x10000 then
    return char(0xE0 + floor(cp / 4096), 0x80 + floor(cp / 64) % 64, 0x80 + cp % 64)
  end
  return char(0xF0 + floor(cp / 262144), 0x80 + floor(cp / 4096) % 64, 0x80 + floor(cp / 64) % 64, 0x80 + cp % 64)
end

local function hex4(s, i)
  local h = s:sub(i, i + 3)
  if not h:find("^%x%x%x%x$") then
    fail(i, "bad \\u escape")
  end
  return tonumber(h, 16)
end

local function decode_string(s, i)
  local parts, n = {}, 0
  local j = i + 1
  while true do
    local k = s:find('[\0-\31"\\]', j)
    if not k then
      fail(i, "unterminated string")
    end
    if k > j then
      n = n + 1
      parts[n] = s:sub(j, k - 1)
    end
    local c = s:sub(k, k)
    if c == '"' then
      return concat(parts, "", 1, n), k + 1
    elseif c == "\\" then
      local e = s:sub(k + 1, k + 1)
      if e == "u" then
        local cp = hex4(s, k + 2)
        j = k + 6
        if cp >= 0xD800 and cp <= 0xDBFF then
          if s:sub(j, j + 1) == "\\u" then
            local lo = hex4(s, j + 2)
            if lo >= 0xDC00 and lo <= 0xDFFF then
              cp = 0x10000 + (cp - 0xD800) * 1024 + (lo - 0xDC00)
              j = j + 6
            else
              cp = 0xFFFD
            end
          else
            cp = 0xFFFD
          end
        elseif cp >= 0xDC00 and cp <= 0xDFFF then
          cp = 0xFFFD
        end
        n = n + 1
        parts[n] = utf8_char(cp)
      elseif ESCAPES[e] then
        n = n + 1
        parts[n] = ESCAPES[e]
        j = k + 2
      else
        fail(k, "bad escape")
      end
    else
      fail(k, "control character in string")
    end
  end
end

local function decode_number(s, i)
  local j = i
  if s:sub(j, j) == "-" then
    j = j + 1
  end
  local c = s:sub(j, j)
  if c == "0" then
    j = j + 1
  elseif c:find("^[1-9]$") then
    local _, e = s:find("^%d+", j)
    j = e + 1
  else
    fail(i, "bad number")
  end
  if s:sub(j, j) == "." then
    local _, e = s:find("^%d+", j + 1)
    if not e then
      fail(j, "bad number")
    end
    j = e + 1
  end
  c = s:sub(j, j)
  if c == "e" or c == "E" then
    local _, e = s:find("^[+-]?%d+", j + 1)
    if not e then
      fail(j, "bad number")
    end
    j = e + 1
  end
  local v = tonumber(s:sub(i, j - 1))
  if v == nil then
    fail(i, "bad number")
  end
  return v, j
end

local decode_value

local function decode_array(s, i, depth)
  local t, n = json.array(), 0
  i = skip(s, i + 1)
  if s:sub(i, i) == "]" then
    return t, i + 1
  end
  while true do
    local v
    v, i = decode_value(s, i, depth + 1)
    n = n + 1
    t[n] = v
    i = skip(s, i)
    local c = s:sub(i, i)
    if c == "]" then
      return t, i + 1
    elseif c ~= "," then
      fail(i, "expected ',' or ']'")
    end
    i = skip(s, i + 1)
  end
end

local function decode_object(s, i, depth)
  local t = json.object()
  i = skip(s, i + 1)
  if s:sub(i, i) == "}" then
    return t, i + 1
  end
  while true do
    if s:sub(i, i) ~= '"' then
      fail(i, "expected a string key")
    end
    local k
    k, i = decode_string(s, i)
    i = skip(s, i)
    if s:sub(i, i) ~= ":" then
      fail(i, "expected ':'")
    end
    local v
    v, i = decode_value(s, skip(s, i + 1), depth + 1)
    t[k] = v
    i = skip(s, i)
    local c = s:sub(i, i)
    if c == "}" then
      return t, i + 1
    elseif c ~= "," then
      fail(i, "expected ',' or '}'")
    end
    i = skip(s, i + 1)
  end
end

local LITERALS = { t = { "true", true }, f = { "false", false }, n = { "null", json.null } }

decode_value = function(s, i, depth)
  if depth > MAX_DEPTH then
    fail(i, "nesting deeper than " .. MAX_DEPTH)
  end
  local c = s:sub(i, i)
  if c == "{" then
    return decode_object(s, i, depth)
  elseif c == "[" then
    return decode_array(s, i, depth)
  elseif c == '"' then
    return decode_string(s, i)
  elseif c == "-" or c:find("^%d$") then
    return decode_number(s, i)
  end
  local lit = LITERALS[c]
  if lit and s:sub(i, i + #lit[1] - 1) == lit[1] then
    return lit[2], i + #lit[1]
  end
  if c == "" then
    fail(i, "unexpected end of text")
  end
  fail(i, "unexpected character")
end

function json.decode(s)
  if type(s) ~= "string" then
    return nil, "json: expected a string, got a " .. type(s)
  end
  local ok, v, i = pcall(function()
    return decode_value(s, skip(s, 1), 1)
  end)
  if not ok then
    if type(v) == "table" and v.msg then
      return nil, format("json: %s at byte %d", v.msg, v.pos)
    end
    return nil, "json: " .. tostring(v)
  end
  i = skip(s, i)
  if i <= #s then
    return nil, format("json: unexpected text after the value at byte %d", i)
  end
  return v
end

return json
