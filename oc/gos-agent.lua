--[[ gos-agent.lua: Gregtec OS's OpenComputers agent (WO-7.2, plan 7.9; contract gos-oc-bridge/1 in
docs/contracts/oc_bridge.md). Written for this repo (MIT, see LICENSE at the repo root).

Runs on OpenOS with a Lua 5.3 CPU. Every 5 to 10 s it reads power (LSC, battery buffers) and GT
machines through GTNH 2.9's drivers, every 60 s an AE2 watchlist of items by registry name and
damage (one exact getItemInNetwork per entry; D213), and sends one telemetry message to the core
over a transport: M2 is an HTTP POST to the core, M1 the file mailbox (mailbox.lua, WO-7.3). The
reply may carry commands Clayton has already confirmed; the agent runs only craft, machine_pause,
machine_resume and say, and reports each result in a later message. Sensor lines go out raw; the
core parses them.

  gos-agent [--list | --once] [config]     config: gos-agent.cfg next to this file
    --list   print what discovery finds, then exit (VERIFY V20: is gt_machine there?)
    --once   send one message and say whether the core answered; if the reply carried commands,
             keep going (at most 30 s) until their results are reported, then exit
  Stop it with Ctrl+C.

Cost per cycle (non-direct calls take one server tick each, direct ones none): each storage entry
(LSC) 2, at most sensor_per_cycle getSensorInformation, getCpus 1, the tps card 1, each tracked craft
1 to 3, the POST about 3 to 5; every ae2_s seconds one getItemInNetwork per watched entry.
Everything else is a direct call. Between cycles it waits in event.pull, so it never holds the
server tick, and every table it keeps is bounded.

Keep this file valid Lua 5.2 (no //, no bitwise operators): OC computers default to 5.2, and the
version check below must be able to run there.
]]

local args = table.pack(...)

if _VERSION ~= "Lua 5.3" or math.type == nil then
  local msg = "gos-agent needs a Lua 5.3 CPU; this computer runs " .. tostring(_VERSION)
    .. ". Hold the CPU (or APU), shift-right-click to switch it to Lua 5.3, put it back and reboot."
  if args[1] == "--lib" then
    return nil, msg
  end
  print(msg)
  return 1
end

local VERSION = "0.1.0"
local CONTRACT_V = 1

-- This file's folder: json.lua (and mailbox.lua, WO-7.3) live next to it.
local function own_dir()
  local info = debug and debug.getinfo and debug.getinfo(1, "S")
  local src = info and info.source or ""
  return src:match("^[=@](.+)/[^/]*$")
end

local DIR = own_dir() or "/home/gos"

local function load_module(name)
  local chunk, err = loadfile(DIR .. "/" .. name .. ".lua")
  if not chunk then
    return nil, err
  end
  local ok, mod = pcall(chunk)
  if not ok then
    return nil, mod
  end
  return mod
end

local json = assert(load_module("json"))
local component = require("component")
local computer = require("computer")
local event = require("event")
local filesystem = require("filesystem")

local M = { VERSION = VERSION, CONTRACT_V = CONTRACT_V, json = json, DIR = DIR }

local floor, mtype, tointeger = math.floor, math.type, math.tointeger

-- pcall that lets OpenOS's hard interrupt through: Ctrl+Alt+C raises "interrupted" out of the
-- pull in progress, and a sleep inside a command or an exchange must not swallow it.
local function try(fn, ...)
  local r = table.pack(pcall(fn, ...))
  if not r[1] and r[2] == "interrupted" then
    error("interrupted", 0)
  end
  return table.unpack(r, 1, r.n)
end

---------------------------------------------------------------------------------------------
-- Limits (bounded tables keep memory flat over hours)

local MAX_SENSOR_LINES = 32 -- an LSC returns 24
local MAX_SENSOR_LINE = 200 -- bytes per line
local SENSOR_BUDGET = 8192 -- bytes of sensor lines in one message; the rest go in a later one
local MAX_ERRORS = 8 -- error records waiting for an ack
local MAX_RESULTS = 16 -- command results waiting for an ack
local MAX_DONE = 32 -- executed command ids remembered (and persisted) against re-delivery
local MAX_CMDS = 8 -- commands taken from one reply
local MAX_CPUS = 32 -- crafting CPUs reported
local MAX_CRAFT_ENTRIES = 16 -- crafts listed: running, or finished and not acknowledged yet
local MAX_NAME = 128 -- registry and meta names (the contract's limit)
local MAX_ERR_MSG = 200
local MAX_SAY = 256
local CRAFT_COMPUTE_MAX_S = 300 -- AE2 still planning after this long: give up on the result
local ONCE_MAX_S = 30 -- --once waits this long at most for the results of commands it ran

M.ALLOWED = { craft = true, machine_pause = true, machine_resume = true, say = true }
M.MAX_BODY = 98304 -- bytes of JSON: over it, sensor lines and then the watchlist wait (D214)
-- How Clayton confirmed a command (D59); the contract's four values, nothing else.
M.CONFIRMED_VIA = { voice = true, dashboard = true, phone = true, discord = true }

---------------------------------------------------------------------------------------------
-- Config

M.DEFAULTS = {
  transport = "auto", -- auto: http when an internet card is in, else the mailbox (WO-7.3)
  url = "http://127.0.0.1:8765/oc/telemetry", -- 127.0.0.1, never localhost (plan App. C)
  token = false, -- the bearer token the core gave this agent (required for http)
  cycle_s = 5, -- power and machines (plan 7.9: 5 to 10 s)
  ae2_s = 60, -- AE2 watchlist
  sensor_s = 60, -- how old a machine's sensor lines may get
  sensor_per_cycle = 2, -- getSensorInformation calls per cycle at most (one tick each)
  request_timeout_s = 12, -- one exchange, connect to last byte
  reply_max = 8192, -- bytes; the contract says the core replies with at most 2048
  backoff_max_s = 60,
  discover_s = 300, -- full rediscovery even without component signals
  watch = {}, -- AE2 watchlist: { {name = "registry:name", damage = 0}, ... }
  power_names = { "multimachine.supercapacitor" }, -- gt_machine names read as storage (the LSC)
  ignore = {}, -- component address prefixes to leave out
  me = false, -- address prefix of the AE2 component to use (default: the first controller)
  max_machines = 32,
  max_power = 16,
  max_watch = 64,
  max_crafts = 8,
  craft_max = 1000000, -- largest craft amount the agent accepts
  log = true, -- true: gos-agent.log next to the config; false: no log file; or a path
  log_max = 32768, -- bytes before the log is rotated to .1
  state = true, -- true: gos-agent.state next to the config; false: none; or a path
  mailbox = false, -- options for the M1 mailbox transport (WO-7.3 checks them)
}

local RANGES = {
  cycle_s = { 5, 10 },
  ae2_s = { 60, 600 },
  sensor_s = { 15, 600 },
  sensor_per_cycle = { 0, 4 },
  request_timeout_s = { 2, 30 },
  reply_max = { 2048, 16384 },
  backoff_max_s = { 10, 300 },
  discover_s = { 30, 3600 },
  max_machines = { 1, 64 },
  max_power = { 1, 32 },
  max_watch = { 0, 64 }, -- one tick per watched entry every ae2_s
  max_crafts = { 1, 16 },
  craft_max = { 1, 1000000000 },
  log_max = { 4096, 262144 },
}

local REGISTRY_NAME = "^[%w_%.%-]+:[^%s%c]+$"
local ADDRESS = "^%x%x%x%x%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%-%x%x%x%x%x%x%x%x%x%x%x%x$"

local function is_list(t)
  if type(t) ~= "table" then
    return false
  end
  local n = 0
  for k in pairs(t) do
    if type(k) ~= "number" then
      return false
    end
    n = n + 1
  end
  return n == #t
end

local function check_watch(list, max)
  if not is_list(list) then
    return nil, "watch must be a list of {name = ..., damage = ...}"
  end
  if #list > max then
    return nil, ("watch has %d entries; max_watch is %d"):format(#list, max)
  end
  local out, seen = {}, {}
  for i, e in ipairs(list) do
    if type(e) ~= "table" then
      return nil, ("watch[%d] must be a table"):format(i)
    end
    if e.id ~= nil or type(e.name) == "number" then
      return nil, ("watch[%d]: use the registry name and damage, never a numeric id (EndlessIDs)"):format(i)
    end
    for k in pairs(e) do
      if k ~= "name" and k ~= "damage" then
        return nil, ("watch[%d]: unknown key %s"):format(i, tostring(k))
      end
    end
    if type(e.name) ~= "string" or not e.name:find(REGISTRY_NAME) or #e.name > MAX_NAME then
      return nil, ("watch[%d]: name must be a registry name like minecraft:iron_ingot"):format(i)
    end
    if mtype(e.damage) ~= "integer" or e.damage < 0 or e.damage > 32767 then
      return nil, ("watch[%d] (%s): damage must be a whole number from 0 to 32767"):format(i, e.name)
    end
    local key = e.name .. "@" .. e.damage
    if not seen[key] then
      seen[key] = true
      out[#out + 1] = { name = e.name, damage = e.damage }
    end
  end
  return out
end

local function check_strings(name, list, min_len)
  if not is_list(list) then
    return nil, name .. " must be a list of strings"
  end
  for i, s in ipairs(list) do
    if type(s) ~= "string" or #s < (min_len or 1) then
      return nil, ("%s[%d] must be a string of at least %d characters"):format(name, i, min_len or 1)
    end
  end
  return true
end

-- Merge a config table over the defaults and check it. Returns the config or nil and why.
function M.check_config(t)
  if type(t) ~= "table" then
    return nil, "the config must be one Lua table"
  end
  local cfg = {}
  for k, v in pairs(M.DEFAULTS) do
    cfg[k] = v
  end
  for k, v in pairs(t) do
    if M.DEFAULTS[k] == nil then
      return nil, "unknown config key " .. tostring(k)
    end
    cfg[k] = v
  end
  if cfg.transport ~= "auto" and cfg.transport ~= "http" and cfg.transport ~= "mailbox" then
    return nil, "transport must be auto, http or mailbox"
  end
  if type(cfg.url) ~= "string" or not cfg.url:find("^https?://[^%s%c]+$") then
    return nil, "url must be an http:// address, e.g. http://127.0.0.1:8765/oc/telemetry"
  end
  if cfg.url:find("^https?://localhost[:/]") or cfg.url:find("^https?://localhost$") then
    return nil, "url: use 127.0.0.1, not localhost (OC's filter allows only 127.0.0.1; localhost may be ::1)"
  end
  if cfg.token ~= false then
    if type(cfg.token) ~= "string" or #cfg.token < 8 or #cfg.token > 512 or cfg.token:find("[%s%c]") then
      return nil, "token must be the 8 to 512 character token the core gave this agent, no spaces"
    end
  end
  for k, r in pairs(RANGES) do
    local v = cfg[k]
    if type(v) ~= "number" or v ~= v or v < r[1] or v > r[2] then
      return nil, ("%s must be a number from %s to %s"):format(k, r[1], r[2])
    end
  end
  for _, k in ipairs({ "sensor_per_cycle", "reply_max", "max_machines", "max_power", "max_watch",
    "max_crafts", "craft_max", "log_max" }) do
    if mtype(cfg[k]) ~= "integer" then
      return nil, k .. " must be a whole number"
    end
  end
  local werr
  cfg.watch, werr = check_watch(cfg.watch, cfg.max_watch)
  if not cfg.watch then
    return nil, werr
  end
  local ok, err = check_strings("power_names", cfg.power_names, 1)
  if not ok then
    return nil, err
  end
  ok, err = check_strings("ignore", cfg.ignore, 3)
  if not ok then
    return nil, err
  end
  if cfg.me ~= false and (type(cfg.me) ~= "string" or #cfg.me < 3) then
    return nil, "me must be false or an address prefix of at least 3 characters"
  end
  for _, k in ipairs({ "log", "state" }) do
    if cfg[k] ~= true and cfg[k] ~= false and type(cfg[k]) ~= "string" then
      return nil, k .. " must be true, false or a path"
    end
  end
  if cfg.mailbox ~= false and type(cfg.mailbox) ~= "table" then
    return nil, "mailbox must be false or a table of mailbox options"
  end
  cfg.power_set = {}
  for _, n in ipairs(cfg.power_names) do
    cfg.power_set[n] = true
  end
  return cfg
end

local function read_file(path)
  local h, err = filesystem.open(path, "r")
  if not h then
    return nil, err
  end
  local parts = {}
  while true do
    local chunk = h:read(2048)
    if not chunk then
      break
    end
    parts[#parts + 1] = chunk
  end
  h:close()
  return table.concat(parts)
end

local function write_file(path, text)
  local h, err = filesystem.open(path, "w")
  if not h then
    return nil, err
  end
  local ok, werr = h:write(text)
  h:close()
  if not ok then
    return nil, werr
  end
  return true
end

local function dirname(path)
  return path:match("^(.*)/[^/]*$") or "."
end

-- Read a config file: one Lua table, comments allowed. It is evaluated with no globals, so it
-- can't reach the computer's libraries, but it is still Lua (string methods and function literals
-- work): it is Clayton's file, trusted like the program itself.
function M.load_config(path)
  local text, err = read_file(path)
  if not text then
    return nil, ("no config at %s (%s): copy gos-agent.cfg.example there and fill in the token"):format(path, tostring(err))
  end
  local chunk, perr = load("return " .. text, "=" .. path, "t", {})
  if not chunk then
    return nil, "config: " .. tostring(perr)
  end
  local ok, t = pcall(chunk)
  if not ok then
    return nil, "config: " .. tostring(t)
  end
  local cfg, cerr = M.check_config(t)
  if not cfg then
    return nil, "config: " .. cerr
  end
  local base = dirname(path)
  cfg.log_path = cfg.log == true and base .. "/gos-agent.log" or cfg.log or nil
  cfg.state_path = cfg.state == true and base .. "/gos-agent.state" or cfg.state or nil
  return cfg
end

---------------------------------------------------------------------------------------------
-- Small helpers

local function now()
  return computer.uptime()
end

local function round2(x)
  return floor(x * 100 + 0.5) / 100
end

local function clip(s, n)
  s = tostring(s)
  if #s > n then
    return s:sub(1, n)
  end
  return s
end

local function int(v)
  if mtype(v) == "integer" then
    return v
  end
  if type(v) == "number" and v == v and v > -2 ^ 63 and v < 2 ^ 63 then
    return tointeger(floor(v))
  end
  return nil
end

local function bool(v)
  if type(v) == "boolean" then
    return v
  end
  return nil
end

local function short(addr)
  return tostring(addr):sub(1, 8)
end

local function gone(err)
  return tostring(err):find("no such component", 1, true) ~= nil
end

---------------------------------------------------------------------------------------------
-- Log: a bounded file next to the config, and the screen for warnings

function M.log(st, level, msg)
  msg = tostring(msg)
  if level ~= "debug" then
    st.out(("[gos-agent] %s"):format(msg))
  end
  local path = st.cfg.log_path
  if not path then
    return
  end
  local line = ("%9.2f %-5s %s\n"):format(now(), level, msg)
  if st.log_bytes == nil then
    st.log_bytes = filesystem.exists(path) and (filesystem.size(path) or 0) or 0
  end
  if st.log_bytes + #line > st.cfg.log_max then
    if filesystem.exists(path .. ".1") then
      filesystem.remove(path .. ".1")
    end
    filesystem.rename(path, path .. ".1")
    st.log_bytes = 0
  end
  local h = filesystem.open(path, "a")
  if h then
    h:write(line)
    h:close()
    st.log_bytes = st.log_bytes + #line
  end
end

-- Errors go out in the next messages until the core acks them; a repeat is counted, not added,
-- and the same error reaches the log at most once in 5 minutes.
function M.record_error(st, where, msg)
  msg = clip(msg, MAX_ERR_MSG)
  local t = round2(now())
  local rec
  for _, e in ipairs(st.errors) do
    if e.where == where and e.msg == msg then
      rec = e
      break
    end
  end
  if rec then
    rec.count, rec.at, rec.seq = rec.count + 1, t, nil
  else
    if #st.errors >= MAX_ERRORS then
      table.remove(st.errors, 1)
    end
    st.errors[#st.errors + 1] = { at = t, where = where, msg = msg, count = 1 }
  end
  local key = where .. ": " .. msg
  if not st.logged[key] or t - st.logged[key] >= 300 then
    if not st.logged[key] then
      st.logged_n = st.logged_n + 1
      if st.logged_n > 32 then
        st.logged, st.logged_n = {}, 1
      end
    end
    st.logged[key] = t
    M.log(st, "warn", key)
  end
end

-- A component call: true and the results, or false and the reason. OC raises only for a missing
-- component or method and for bad arguments. A method whose Java side threw anything else comes
-- back from component.invoke as nil, reason ("unknown error" when the exception has no message),
-- which is a failed call here too, never a reading of nil.
local function call(addr, method, ...)
  local r = table.pack(pcall(component.invoke, addr, method, ...))
  if r[1] and r[2] == nil and r[3] ~= nil then
    return false, r[3]
  end
  return table.unpack(r, 1, r.n)
end

---------------------------------------------------------------------------------------------
-- Discovery

local GT_TYPES = { gt_machine = true, gt_batterybuffer = true, lsc = true }
local ME_TYPES = { me_controller = 1, me_interface = 2 }

local function ignored(cfg, addr)
  for _, p in ipairs(cfg.ignore) do
    if addr:sub(1, #p) == p then
      return true
    end
  end
  return false
end

-- Computronics returns x, y, z (its doc string says a table; take either).
local function coords(addr)
  local ok, x, y, z = call(addr, "getCoordinates")
  if ok and type(x) == "table" then
    x, y, z = x[1], x[2], x[3]
  end
  if ok and int(x) and int(y) and int(z) then
    return json.array({ int(x), int(y), int(z) })
  end
  return nil
end

-- Walk the component list and sort what the agent can use into power, machines, AE2 and the rest.
-- Entries that were already known keep their state (sensor lines, pending flags).
function M.discover(st)
  local cfg, old = st.cfg, st.inv and st.inv.by_addr or {}
  local inv = { power = {}, machines = {}, counts = json.object(), by_addr = {}, skipped = 0 }
  local found = {}
  for addr, ctype in component.list() do
    found[#found + 1] = { addr, ctype }
  end
  table.sort(found, function(a, b)
    return a[1] < b[1]
  end)
  local me_rank
  for _, f in ipairs(found) do
    local addr, ctype = f[1], f[2]
    if ctype:find("^[%w_]+$") then -- the contract's type names; any other isn't one the agent uses
      inv.counts[ctype] = (inv.counts[ctype] or 0) + 1
    end
    if not ignored(cfg, addr) then
      if GT_TYPES[ctype] then
        local e = old[addr] or { addr = addr, type = ctype }
        if not e.name then -- new, or getName failed last time
          if ctype == "lsc" then
            e.name = "multimachine.supercapacitor"
          else
            local ok, name = call(addr, "getName")
            if ok and type(name) == "string" and #name <= MAX_NAME then
              e.name = name
            end
          end
        end
        e.xyz = e.xyz or coords(addr)
        local kind
        if ctype == "gt_batterybuffer" then
          kind = "battery_buffer"
        elseif ctype == "lsc" or (e.name and cfg.power_set[e.name]) then
          kind = "storage"
        end
        if kind ~= e.kind then
          -- First sight, or a name that came late moved it (a machine that is an LSC): no old readings.
          e = { addr = addr, type = ctype, name = e.name, xyz = e.xyz, kind = kind }
        end
        local list, max = inv.machines, cfg.max_machines
        if e.kind then
          list, max = inv.power, cfg.max_power
        end
        if #list < max then
          list[#list + 1] = e
          inv.by_addr[addr] = e
        else
          inv.skipped = inv.skipped + 1
        end
      elseif ME_TYPES[ctype] then
        local rank = ME_TYPES[ctype]
        if cfg.me and addr:sub(1, #cfg.me) == cfg.me then
          rank = 0
        end
        if (not me_rank or rank < me_rank) and (not cfg.me or rank == 0) then
          local ok, methods = pcall(component.methods, addr)
          -- An ME interface on a cable (a part) has no network methods in OC 1.12.68 [C 2.5].
          if ok and type(methods) == "table" and methods.getItemInNetwork ~= nil and methods.getCpus ~= nil then
            inv.me = { addr = addr, type = ctype }
            me_rank = rank
          end
        end
      elseif ctype == "chat_box" then
        inv.chat = inv.chat or addr
      elseif ctype == "tps_card" then
        inv.tps = inv.tps or addr
      end
    end
  end
  if st.inv and st.inv.me and inv.me and st.inv.me.addr == inv.me.addr then
    inv.me = st.inv.me
  end
  st.inv = inv
  st.rediscover = false
  st.next_discover = now() + cfg.discover_s
  if inv.skipped > 0 then
    M.record_error(st, "discover", ("%d GT components over max_machines/max_power were left out"):format(inv.skipped))
  end
  return inv
end

-- Drop one entry (its block is gone) and look again at the start of the next cycle.
local function lost(st, e)
  local inv = st.inv
  inv.by_addr[e.addr] = nil
  for _, list in ipairs({ inv.power, inv.machines }) do
    for i = #list, 1, -1 do
      if list[i] == e then
        table.remove(list, i)
      end
    end
  end
  st.rediscover = true
  M.record_error(st, "discover", short(e.addr) .. " (" .. tostring(e.name or e.type) .. ") is gone")
end

---------------------------------------------------------------------------------------------
-- Sampling

-- One reading per field; a failing call leaves the field out and is recorded. A method the
-- component doesn't have is not tried again.
local function read(st, e, where, method)
  if e.skip and e.skip[method] then
    return nil, true
  end
  local ok, v = call(e.addr, method)
  if ok then
    return v, true
  end
  if gone(v) then
    return nil, false
  end
  if tostring(v):find("no such method", 1, true) then
    e.skip = e.skip or {}
    e.skip[method] = true
  end
  M.record_error(st, where, short(e.addr) .. " " .. method .. ": " .. tostring(v))
  return nil, true
end

local function sample_fields(st, e, where, fields)
  for _, f in ipairs(fields) do
    local v, alive = read(st, e, where, f[2])
    if not alive then
      lost(st, e)
      return false
    end
    e[f[1]] = f[3](v)
  end
  return true
end

local DECIMAL = function(v)
  if type(v) == "string" and v:find("^%d+$") and #v <= 80 then
    return v
  end
  return nil
end

-- A stored amount: a whole number, never negative (anything else is left out).
local function nonneg(v)
  v = int(v)
  if v and v >= 0 then
    return v
  end
  return nil
end

local BUFFER_FIELDS = {
  { "eu", "getEUStored", nonneg },
  { "eu_max", "getEUMaxStored", nonneg },
  { "eu_in", "getEUInputAverage", int },
  { "eu_out", "getEUOutputAverage", int },
}
local STORAGE_FIELDS = {
  { "eu_str", "getStoredEUString", DECIMAL },
  { "eu_max_str", "getEUCapacityString", DECIMAL },
  { "eu_in", "getEUInputAverage", int },
  { "eu_out", "getEUOutputAverage", int },
}
local MACHINE_FIELDS = {
  { "active", "isMachineActive", bool },
  { "allowed", "isWorkAllowed", bool },
  { "work", "hasWork", bool },
  { "progress", "getWorkProgress", int },
  { "progress_max", "getWorkMaxProgress", int },
  { "eu", "getEUStored", nonneg },
  { "eu_max", "getEUMaxStored", nonneg },
}

local function sample_power(st)
  local list = st.inv.power
  for i = #list, 1, -1 do
    local e = list[i]
    sample_fields(st, e, "power", e.kind == "storage" and STORAGE_FIELDS or BUFFER_FIELDS)
  end
end

local function sample_machines(st)
  local list = st.inv.machines
  for i = #list, 1, -1 do
    sample_fields(st, list[i], "machines", MACHINE_FIELDS)
  end
end

-- getSensorInformation costs a tick, so each entry is read when its lines are sensor_s old, oldest
-- first, at most sensor_per_cycle per cycle. Lines are kept raw (2.9: "key\\arg\\arg" or text).
local function sample_sensors(st)
  local due, t = {}, now()
  for _, list in ipairs({ st.inv.power, st.inv.machines }) do
    for _, e in ipairs(list) do
      if not e.sensor_at or t - e.sensor_at >= st.cfg.sensor_s then
        due[#due + 1] = e
      end
    end
  end
  table.sort(due, function(a, b)
    return (a.sensor_at or -1) < (b.sensor_at or -1) or ((a.sensor_at or -1) == (b.sensor_at or -1) and a.addr < b.addr)
  end)
  for i = 1, math.min(#due, st.cfg.sensor_per_cycle) do
    local e = due[i]
    local ok, lines = call(e.addr, "getSensorInformation")
    if not ok then
      if gone(lines) then
        lost(st, e)
      else
        e.sensor_at = round2(now()) -- try again in sensor_s, not every cycle
        M.record_error(st, "sensor", short(e.addr) .. ": " .. tostring(lines))
      end
    else
      local out, bytes = json.array(), 0
      if type(lines) == "table" then
        for _, line in ipairs(lines) do
          if #out >= MAX_SENSOR_LINES then
            break
          end
          if type(line) == "string" then
            out[#out + 1] = clip(line, MAX_SENSOR_LINE)
            bytes = bytes + #out[#out]
          end
        end
      end
      e.sensor, e.sensor_bytes, e.sensor_at, e.sensor_seq = out, bytes, round2(now()), nil
    end
  end
end

local function sample_cpus(st)
  local me = st.inv.me
  local ok, cpus = call(me.addr, "getCpus")
  if not ok then
    if gone(cpus) then
      st.inv.me, st.rediscover = nil, true
      M.record_error(st, "ae2", short(me.addr) .. " is gone")
    else
      M.record_error(st, "ae2", "getCpus: " .. tostring(cpus))
    end
    me.cpus = nil
    return
  end
  local out = json.array()
  if type(cpus) == "table" then
    for _, c in ipairs(cpus) do
      if #out >= MAX_CPUS then
        -- Once per discovery (st.inv is new each time), not in every message.
        if not st.inv.cpus_cut then
          st.inv.cpus_cut = true
          M.record_error(st, "ae2", ("%d crafting CPUs; the first %d are reported"):format(#cpus, MAX_CPUS))
        end
        break
      end
      if type(c) == "table" then
        out[#out + 1] = {
          name = type(c.name) == "string" and clip(c.name, 64) or "",
          storage = nonneg(c.storage) or 0,
          coprocessors = nonneg(c.coprocessors) or 0,
          busy = c.busy == true,
        }
      end
    end
  end
  me.cpus = out
end

-- The watchlist: one getItemInNetwork(name, damage) per watched entry, an exact lookup of the
-- stack with that registry name and damage and no NBT (AE2 treats OC's default empty tag as none),
-- which returns one small table or nil. Never getItemsInNetworkById: it matches the Item only, so
-- a name like gregtech:gt.metaitem.01 brings back every variant the network holds, converted on
-- the server thread and held in this computer's memory at once (D213). Never getItemsInNetwork or
-- allItems [C 2.5].
local function sample_items(st)
  local me = st.inv.me
  local items = json.array()
  for _, w in ipairs(st.cfg.watch) do
    local ok, s = call(me.addr, "getItemInNetwork", w.name, w.damage)
    if not ok then
      if gone(s) then
        st.inv.me, st.rediscover = nil, true
        M.record_error(st, "ae2", short(me.addr) .. " is gone")
        return
      end
      M.record_error(st, "ae2", ("getItemInNetwork %s@%d: %s"):format(w.name, w.damage, tostring(s)))
    elseif type(s) == "table" and nonneg(s.size) == nil then
      M.record_error(st, "ae2", ("getItemInNetwork %s@%d: no usable size"):format(w.name, w.damage))
    else
      -- nil: none stored and no pattern, or a name the game doesn't know.
      local found = type(s) == "table"
      items[#items + 1] = {
        name = w.name,
        damage = w.damage,
        size = found and nonneg(s.size) or 0,
        craftable = found and s.isCraftable == true,
      }
    end
  end
  me.items, me.items_at, me.items_seq = items, round2(now()), nil
end

local function sample_tps(st)
  local ok, ms = call(st.inv.tps, "getOverallTickTime")
  if ok and type(ms) == "number" and ms == ms and ms >= 0 then
    st.tick_ms = round2(ms)
  else
    st.tick_ms = nil
    if not ok then
      if gone(ms) then
        st.inv.tps, st.rediscover = nil, true
      else
        M.record_error(st, "tps", tostring(ms))
      end
    end
  end
end

---------------------------------------------------------------------------------------------
-- Results, crafts and the executed-id memory

local function queue_result(st, r)
  for i, q in ipairs(st.results) do
    if q.id == r.id then
      table.remove(st.results, i)
      break
    end
  end
  if #st.results >= MAX_RESULTS then
    local dropped = table.remove(st.results, 1)
    M.record_error(st, "command", "result of command " .. dropped.id .. " dropped: too many unacked results")
  end
  local copy = {}
  for k, v in pairs(r) do
    copy[k] = v
  end
  copy.seq = nil
  st.results[#st.results + 1] = copy
end

local function save_state(st)
  local path = st.cfg.state_path
  if not path then
    return
  end
  local done = json.array()
  for _, id in ipairs(st.done_order) do
    local r = st.done[id]
    done[#done + 1] = { id = id, kind = r.kind, ok = r.ok, state = r.state, err = r.err }
  end
  local ok, err = write_file(path .. ".tmp", json.encode({ v = 1, done = done }))
  if ok then
    ok, err = filesystem.rename(path .. ".tmp", path)
  end
  if not ok then
    M.record_error(st, "state", "can't save " .. path .. ": " .. tostring(err))
  end
end

-- Remember what each command id came to, so a re-delivered command is reported, never run again.
local function remember(st, id, r)
  if st.done[id] == nil then
    st.done_order[#st.done_order + 1] = id
    if #st.done_order > MAX_DONE then
      local old = table.remove(st.done_order, 1)
      st.done[old] = nil
    end
  end
  st.done[id] = r
  save_state(st)
end

local function load_state(st)
  local path = st.cfg.state_path
  if not path or not filesystem.exists(path) then
    return
  end
  local text = read_file(path)
  local doc = text and json.decode(text)
  if type(doc) ~= "table" or not json.is_array(doc.done) then
    M.record_error(st, "state", "ignored an unreadable " .. path)
    return
  end
  for _, r in ipairs(doc.done) do
    if type(r) == "table" and mtype(r.id) == "integer" and type(r.state) == "string" then
      local rec = {
        id = r.id,
        kind = type(r.kind) == "string" and r.kind or "?",
        ok = r.ok == true,
        state = r.state,
        at = round2(now()),
      }
      if type(r.err) == "string" then
        rec.err = clip(r.err, MAX_ERR_MSG)
      elseif not rec.ok then
        rec.err = "no reason was saved"
      end
      if rec.state == "pending" then
        -- It was still running (an AE2 craft planning) when the agent stopped: say so once.
        rec.ok, rec.state = false, "failed"
        rec.err = "the agent restarted before AE2 answered; the craft may still run, check AE2"
        queue_result(st, rec)
      end
      st.done_order[#st.done_order + 1] = rec.id
      st.done[rec.id] = rec
    end
  end
  while #st.done_order > MAX_DONE do
    st.done[table.remove(st.done_order, 1)] = nil
  end
end

local function result(id, kind, ok, state, err)
  local r = { id = id, kind = clip(kind, 32), ok = ok, state = state, at = round2(now()) }
  if err then
    r.err = clip(err, MAX_ERR_MSG)
  end
  return r
end

local function craft_state(st, c, state, err)
  c.state, c.at, c.seq = state, round2(now()), nil
  if err then
    c.err = clip(err, MAX_ERR_MSG)
  end
end

-- OC keeps a crafting status across a world reload (the computer resumes) but not its AE2 link:
-- the status then answers "no link", isCanceled true and isDone false for a craft that may well
-- still run (OC 1.12.68 NetworkControl CraftingStatus.save/load). That is reported as lost track,
-- never as canceled. Saving alone (an autosave) also sets the live status's failed flag while AE2
-- is still planning, so a job AE2 accepted afterwards answers hasFailed with true, "no link" too;
-- isDone tells them apart: with a link it answers from AE2's job, one value and no reason.
local NO_LINK = "no link"
local LOST = "lost track of it after a world reload (OC keeps no AE2 link); the craft may still run, check AE2"

local function has_link(status)
  local ok, _, why = pcall(status.isDone)
  return ok and why == nil
end

local function poll_crafts(st)
  for _, c in ipairs(st.crafts) do
    if c.state == "computing" then
      local ok, computing = pcall(c.status.isComputing)
      if not ok then
        craft_state(st, c, "failed", computing)
        c.result = result(c.id, "craft", false, "failed", computing)
      elseif computing == false then
        local okf, failed, reason = pcall(c.status.hasFailed)
        if okf and failed and reason == NO_LINK and has_link(c.status) then
          failed = false -- an autosave while AE2 planned, and AE2 has taken the job since
        end
        if okf and failed then
          reason = reason == NO_LINK and LOST or reason or "request failed"
          craft_state(st, c, "failed", reason)
          c.result = result(c.id, "craft", false, "failed", reason)
        elseif okf then
          craft_state(st, c, "crafting")
          c.result = result(c.id, "craft", true, "submitted")
        else
          craft_state(st, c, "failed", failed)
          c.result = result(c.id, "craft", false, "failed", failed)
        end
      elseif now() - c.since > CRAFT_COMPUTE_MAX_S then
        craft_state(st, c, "failed", "AE2 still planning after " .. CRAFT_COMPUTE_MAX_S .. " s")
        c.result = result(c.id, "craft", false, "failed", c.err)
      end
      if c.result then
        queue_result(st, c.result)
        remember(st, c.id, c.result)
        c.result = nil
      end
    elseif c.state == "crafting" then
      local ok, done, why = pcall(c.status.isDone)
      if ok and done == true then
        craft_state(st, c, "done")
      elseif ok and why == NO_LINK then
        craft_state(st, c, "failed", LOST)
      else
        local okc, canceled = pcall(c.status.isCanceled)
        if okc and canceled == true then
          craft_state(st, c, "canceled")
        elseif not ok then
          craft_state(st, c, "failed", done)
        end
      end
    end
  end
end

---------------------------------------------------------------------------------------------
-- Commands (plan 7.9 safe actions). The core sends only actions Clayton confirmed (D25, D59);
-- this side checks the kind is allowed, the command says how it was confirmed, and the arguments.

local HANDLERS = {}

local function refused(id, kind, why)
  return result(id, kind, false, "refused", why)
end

local function failed(id, kind, why)
  return result(id, kind, false, "failed", why)
end

function HANDLERS.craft(st, id, a)
  local me = st.inv.me
  if not me then
    return failed(id, "craft", "no AE2 controller or interface found")
  end
  if type(a.name) ~= "string" or not a.name:find(REGISTRY_NAME) or #a.name > MAX_NAME then
    return refused(id, "craft", "name must be a registry name")
  end
  if mtype(a.damage) ~= "integer" or a.damage < 0 or a.damage > 32767 then
    return refused(id, "craft", "damage must be a whole number from 0 to 32767")
  end
  if mtype(a.amount) ~= "integer" or a.amount < 1 or a.amount > st.cfg.craft_max then
    return refused(id, "craft", "amount must be a whole number from 1 to " .. st.cfg.craft_max)
  end
  if a.cpu ~= nil and a.cpu ~= json.null and (type(a.cpu) ~= "string" or #a.cpu > 64) then
    return refused(id, "craft", "cpu must be a CPU name")
  end
  local active = 0
  for _, c in ipairs(st.crafts) do
    if c.state == "computing" or c.state == "crafting" then
      active = active + 1
    end
  end
  if active >= st.cfg.max_crafts then
    return failed(id, "craft", "already tracking " .. active .. " crafts (max_crafts)")
  end
  -- One exact lookup (the stack with this name and damage, no NBT). getCraftables would convert
  -- every craftable in the network on the server thread before filtering.
  local ok, craftable = call(me.addr, "getCraftable", { name = a.name, damage = a.damage }, "item")
  if not ok then
    return failed(id, "craft", "getCraftable: " .. tostring(craftable))
  end
  if type(craftable) ~= "table" then
    return failed(id, "craft", ("AE2 has no pattern for %s@%d"):format(a.name, a.damage))
  end
  local okr, status, reason
  if type(a.cpu) == "string" then
    okr, status, reason = pcall(craftable.request, a.amount, true, a.cpu)
  else
    okr, status, reason = pcall(craftable.request, a.amount)
  end
  if not okr then
    return failed(id, "craft", "request: " .. tostring(status))
  end
  if not status then
    return failed(id, "craft", reason or "AE2 refused the request")
  end
  if #st.crafts >= MAX_CRAFT_ENTRIES then
    -- Fewer than max_crafts (at most 16) run, so a finished one is waiting for its ack: drop the oldest.
    for i, c in ipairs(st.crafts) do
      if c.state ~= "computing" and c.state ~= "crafting" then
        table.remove(st.crafts, i)
        M.record_error(st, "command", "craft " .. c.id .. "'s final state was dropped: too many unacknowledged crafts")
        break
      end
    end
  end
  st.crafts[#st.crafts + 1] = {
    id = id,
    name = a.name,
    damage = a.damage,
    amount = a.amount,
    status = status,
    state = "computing",
    at = round2(now()),
    since = now(),
  }
  return nil -- the result follows once AE2 has planned it
end

local function set_work(st, id, kind, a, allowed)
  local e = type(a.addr) == "string" and st.inv.by_addr[a.addr] or nil
  if not e or e.kind then
    return refused(id, kind, "not a machine this agent found: " .. clip(a.addr, 40))
  end
  local ok, err = call(e.addr, "setWorkAllowed", allowed)
  if not ok then
    return failed(id, kind, "setWorkAllowed: " .. tostring(err))
  end
  os.sleep(0.1) -- the driver applies it on the next server tick
  local ok2, now_allowed = call(e.addr, "isWorkAllowed")
  if not ok2 then
    return failed(id, kind, "isWorkAllowed: " .. tostring(now_allowed))
  end
  if now_allowed == allowed then
    e.allowed = allowed
    return result(id, kind, true, "done")
  end
  return failed(id, kind, "the machine still says work allowed = " .. tostring(now_allowed))
end

function HANDLERS.machine_pause(st, id, a)
  return set_work(st, id, "machine_pause", a, false)
end

function HANDLERS.machine_resume(st, id, a)
  return set_work(st, id, "machine_resume", a, true)
end

-- Chat-box text loses control characters and section-sign colour codes.
local function plain_text(s)
  s = s:gsub("\194\167.", ""):gsub("%c", " ")
  return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

function HANDLERS.say(st, id, a)
  if type(a.text) ~= "string" then
    return refused(id, "say", "text must be a string")
  end
  local text = plain_text(a.text)
  if text == "" or #text > MAX_SAY then
    return refused(id, "say", "text must be 1 to " .. MAX_SAY .. " bytes")
  end
  if not st.inv.chat then
    return failed(id, "say", "no chat box found")
  end
  local ok, res = call(st.inv.chat, "say", text)
  if not ok then
    return failed(id, "say", tostring(res))
  end
  if res == false then
    return failed(id, "say", "the chat box refused the text")
  end
  return result(id, "say", true, "done")
end

function M.run_command(st, cmd)
  if type(cmd) ~= "table" or json.is_array(cmd) then
    M.record_error(st, "command", "a command that is not an object was ignored")
    return
  end
  local id = cmd.id
  if mtype(id) ~= "integer" or id < 1 then
    M.record_error(st, "command", "a command without a positive whole-number id was ignored")
    return
  end
  local seen = st.done[id]
  if seen then
    if seen.state ~= "pending" then
      queue_result(st, seen) -- delivered again: report it again, never run it twice
    end
    return
  end
  local kind = type(cmd.kind) == "string" and cmd.kind or tostring(cmd.kind)
  local r
  if not M.ALLOWED[kind] then
    r = refused(id, kind, "kind not allowed")
  elseif not M.CONFIRMED_VIA[cmd.confirmed_via] then
    r = refused(id, kind, "not confirmed (confirmed_via must be voice, dashboard, phone or discord)")
  elseif not json.is_object(cmd.args) then
    r = refused(id, kind, "args must be an object")
  else
    local ok, out = try(HANDLERS[kind], st, id, cmd.args)
    if not ok then
      out = failed(id, kind, "agent error: " .. tostring(out))
    end
    r = out
  end
  if r then
    remember(st, id, r)
    queue_result(st, r)
    M.log(st, "info", ("command %d %s: %s%s"):format(id, kind, r.state, r.err and (" (" .. r.err .. ")") or ""))
  else
    remember(st, id, { id = id, kind = kind, ok = false, state = "pending" })
    M.log(st, "info", ("command %d %s: sent to AE2"):format(id, kind))
  end
end

function M.handle_commands(st, cmds)
  if cmds == nil or cmds == json.null then
    return 0
  end
  if not json.is_array(cmds) then
    M.record_error(st, "command", "cmds is not a list; ignored")
    return 0
  end
  if #cmds > MAX_CMDS then
    M.record_error(st, "command", ("a reply carried %d commands; only the first %d were taken"):format(#cmds, MAX_CMDS))
  end
  local n = 0
  for i = 1, math.min(#cmds, MAX_CMDS) do
    M.run_command(st, cmds[i])
    n = n + 1
  end
  return n
end

---------------------------------------------------------------------------------------------
-- The telemetry document (gos-oc-bridge/1, docs/contracts/oc_bridge.md)

-- Fresh sensor lines go until the core acks a message that carried them, as many entries' as fit
-- in SENSOR_BUDGET bytes (one entry's fit: 32 lines of 200 bytes); the others wait for a later
-- message. An entry left out loses its mark, so an ack never drops lines it didn't carry.
local function entry_doc(e, seq, budget)
  local d = { addr = e.addr, type = e.type, name = e.name, xyz = e.xyz }
  if e.kind then
    d.kind = e.kind
    d.eu, d.eu_max, d.eu_str, d.eu_max_str = e.eu, e.eu_max, e.eu_str, e.eu_max_str
    d.eu_in, d.eu_out = e.eu_in, e.eu_out
  else
    d.active, d.allowed, d.work = e.active, e.allowed, e.work
    d.progress, d.progress_max, d.eu, d.eu_max = e.progress, e.progress_max, e.eu, e.eu_max
  end
  if e.sensor then
    local bytes = e.sensor_bytes or 0
    if bytes <= budget.left then
      budget.left = budget.left - bytes
      d.sensor, d.sensor_at = e.sensor, e.sensor_at
      e.sensor_seq = e.sensor_seq or seq
    else
      e.sensor_seq = nil
    end
  end
  return d
end

-- opts.no_sensor and opts.no_items leave the sensor lines or the watchlist out (they wait, their
-- marks cleared like an entry over the sensor budget), for a message over M.MAX_BODY.
function M.build_payload(st, opts)
  opts = opts or {}
  local seq = st.seq
  local budget = { left = opts.no_sensor and 0 or SENSOR_BUDGET }
  local p = {
    v = CONTRACT_V,
    kind = "telemetry",
    agent = VERSION,
    boot = st.boot,
    seq = seq,
    uptime = round2(now()),
    transport = st.transport and st.transport.name or "none",
    cycle_s = st.cfg.cycle_s,
    sample_s = round2(st.sample_s or 0),
    computer = {
      address = computer.address(),
      arch = computer.getArchitecture and computer.getArchitecture() or _VERSION,
      mem_free = int(computer.freeMemory()) or 0,
      mem_total = int(computer.totalMemory()) or 0,
      energy = round2(computer.energy()),
      energy_max = round2(computer.maxEnergy()),
    },
    tick_ms = st.tick_ms,
    components = st.inv.counts,
    power = json.array(),
    machines = json.array(),
    crafts = json.array(),
    results = json.array(),
    errors = json.array(),
  }
  for _, e in ipairs(st.inv.power) do
    p.power[#p.power + 1] = entry_doc(e, seq, budget)
  end
  for _, e in ipairs(st.inv.machines) do
    p.machines[#p.machines + 1] = entry_doc(e, seq, budget)
  end
  local me = st.inv.me
  if me then
    p.ae2 = { addr = me.addr, type = me.type, cpus = me.cpus or json.array() }
    if me.items and not opts.no_items then
      p.ae2.items, p.ae2.items_at = me.items, me.items_at
      me.items_seq = me.items_seq or seq
    elseif me.items then
      me.items_seq = nil
    end
  end
  for _, c in ipairs(st.crafts) do
    p.crafts[#p.crafts + 1] = {
      id = c.id,
      name = c.name,
      damage = c.damage,
      amount = c.amount,
      state = c.state,
      err = c.err,
      at = c.at,
    }
    if c.state == "done" or c.state == "canceled" or c.state == "failed" then
      c.seq = c.seq or seq
    end
  end
  for _, r in ipairs(st.results) do
    p.results[#p.results + 1] = { id = r.id, kind = r.kind, ok = r.ok, state = r.state, err = r.err, at = r.at }
    r.seq = r.seq or seq
  end
  for _, e in ipairs(st.errors) do
    p.errors[#p.errors + 1] = { at = e.at, where = e.where, msg = e.msg, count = e.count }
    e.seq = e.seq or seq
  end
  return p
end

-- The message as JSON, kept under M.MAX_BODY when it can be: over it, the sensor lines and then
-- the watchlist wait for a later message (the core takes up to 256 KB; what remains is bounded
-- by the limits above, D214).
function M.encode_payload(st)
  local body = json.encode(M.build_payload(st))
  if #body <= M.MAX_BODY then
    return body
  end
  local left = "sensor lines wait"
  body = json.encode(M.build_payload(st, { no_sensor = true }))
  if #body > M.MAX_BODY then
    left = "sensor lines and the watchlist wait"
    body = json.encode(M.build_payload(st, { no_sensor = true, no_items = true }))
  end
  -- The same text each time, so a repeat is counted rather than added.
  M.record_error(st, "cycle", ("a message would be over %d bytes: %s for a smaller one"):format(M.MAX_BODY, left))
  return body
end

-- The core has everything up to message n: drop what it acknowledged.
function M.ack(st, n)
  local function keep(list, terminal_only)
    for i = #list, 1, -1 do
      local x = list[i]
      if x.seq and x.seq <= n and (not terminal_only or x.state ~= "computing" and x.state ~= "crafting") then
        table.remove(list, i)
      end
    end
  end
  keep(st.results)
  keep(st.errors)
  keep(st.crafts, true)
  for _, list in ipairs({ st.inv.power, st.inv.machines }) do
    for _, e in ipairs(list) do
      if e.sensor_seq and e.sensor_seq <= n then
        e.sensor, e.sensor_bytes, e.sensor_seq = nil, nil, nil
      end
    end
  end
  local me = st.inv.me
  if me and me.items_seq and me.items_seq <= n then
    me.items, me.items_seq = nil, nil
  end
end

-- The core called a message bad_payload: it can't read what is waiting, so resending it would be
-- refused forever. Drop everything that waits for an ack; the next message is a fresh snapshot.
function M.drop_pending(st)
  local n = #st.results + #st.errors
  st.results, st.errors = {}, {}
  for i = #st.crafts, 1, -1 do
    local c = st.crafts[i]
    if c.state ~= "computing" and c.state ~= "crafting" then
      table.remove(st.crafts, i)
      n = n + 1
    end
  end
  for _, list in ipairs({ st.inv.power, st.inv.machines }) do
    for _, e in ipairs(list) do
      if e.sensor then
        e.sensor, e.sensor_bytes, e.sensor_seq = nil, nil, nil
        n = n + 1
      end
    end
  end
  local me = st.inv.me
  if me and me.items then
    me.items, me.items_seq = nil, nil
    n = n + 1
  end
  M.log(st, "warn", ("the core can't read the messages (bad_payload); dropped %d waiting records"):format(n))
end

---------------------------------------------------------------------------------------------
-- Transports. Interface (the M1 mailbox, WO-7.3, implements the same):
--   t.name          "http" or "mailbox"
--   t.implicit_ack  true when a reply means the core has the message it answers
--   t:exchange(body, seq) -> reply table | nil, err    one message out, the core's reply back
--   t:close()

local Http = {}
Http.__index = Http

function M.http_transport(cfg)
  if not cfg.token then
    return nil, "set token in the config: the core's token for this agent"
  end
  if not component.isAvailable("internet") then
    return nil, "no internet card"
  end
  local inet = require("internet")
  return setmetatable({ name = "http", implicit_ack = true, cfg = cfg, inet = inet }, Http)
end

-- POST the body, then read the reply in 2 KB chunks (OC reads at most 2048 bytes per call) until
-- the stream ends. The handle is closed on every path: OC allows only 4 open connections per card.
-- A request that failed (connection refused, a filtered address, a code of 400 or more, a
-- connection lost in a world reload) doesn't raise: finishConnect and read return nil and the
-- reason, where false and "" mean "not yet" and a lone nil from read the end of the reply.
function Http:exchange(body, seq)
  local cfg = self.cfg
  local headers = {
    ["Content-Type"] = "application/json",
    ["Authorization"] = "Bearer " .. cfg.token,
  }
  local ok, handle = pcall(self.inet.request, cfg.url, body, headers, "POST")
  if not ok then
    return nil, "request: " .. tostring(handle)
  end
  local deadline = now() + cfg.request_timeout_s
  local text
  local okx, err = pcall(function()
    while true do
      local ready, why = handle.finishConnect()
      if ready then
        break
      elseif why ~= nil then
        error(tostring(why), 0)
      end
      if now() >= deadline then
        error("no answer from the core in " .. cfg.request_timeout_s .. " s", 0)
      end
      os.sleep(0.05)
    end
    local code = handle.response()
    if code ~= nil and code ~= 200 then
      error("HTTP " .. tostring(code), 0)
    end
    local parts, total = {}, 0
    while true do
      local chunk, why = handle.read(2048)
      if chunk == nil then
        if why ~= nil then
          error(tostring(why), 0)
        end
        break
      end
      if #chunk > 0 then
        total = total + #chunk
        if total > cfg.reply_max then
          error("reply longer than " .. cfg.reply_max .. " bytes", 0)
        end
        parts[#parts + 1] = chunk
      elseif now() >= deadline then
        error("the core's reply stalled for " .. cfg.request_timeout_s .. " s", 0)
      else
        os.sleep(0.05)
      end
    end
    text = table.concat(parts)
  end)
  pcall(handle.close)
  if not okx then
    if err == "interrupted" then
      error(err, 0) -- Ctrl+Alt+C, after the connection is closed
    end
    return nil, tostring(err)
  end
  local reply, derr = json.decode(text)
  if type(reply) ~= "table" or json.is_array(reply) then
    return nil, "bad reply: " .. tostring(derr or "not an object")
  end
  return reply
end

function Http:close() end

function M.open_transport(st)
  local cfg, which = st.cfg, st.cfg.transport
  if which == "auto" then
    which = component.isAvailable("internet") and "http" or "mailbox"
  end
  if which == "http" then
    return M.http_transport(cfg)
  end
  local mod, err = load_module("mailbox")
  if not mod then
    return nil, "no internet card, and the M1 mailbox (mailbox.lua, WO-7.3) is not installed: " .. tostring(err)
  end
  return mod.new(cfg, { json = json, log = function(level, msg)
    M.log(st, level, msg)
  end })
end

---------------------------------------------------------------------------------------------
-- The cycle

local function sample(st)
  local t0 = now()
  local link_up = st.failures == 0
  sample_power(st)
  sample_machines(st)
  if link_up then
    -- Sensor lines and the watchlist cost ticks; while the core can't be reached they wait.
    sample_sensors(st)
  end
  if st.inv.me then
    sample_cpus(st)
  end
  if st.inv.me and link_up and #st.cfg.watch > 0 and t0 >= st.next_items then
    sample_items(st)
    st.next_items = t0 + st.cfg.ae2_s
  end
  if st.inv.tps then
    sample_tps(st)
  end
  poll_crafts(st)
  st.sample_s = now() - t0
end

local function backoff(st, long)
  local d = st.cfg.cycle_s * 2 ^ (st.failures - 1)
  if long or d > st.cfg.backoff_max_s then
    d = st.cfg.backoff_max_s
  end
  return d
end

function M.handle_reply(st, reply, seq)
  if reply.v ~= nil and reply.v ~= CONTRACT_V then
    return false, "the core speaks contract v" .. tostring(reply.v) .. "; this agent speaks v" .. CONTRACT_V, true
  end
  if reply.ok ~= true then
    local why = type(reply.err) == "string" and reply.err or "no reason given"
    local long = why == "unauthorized" or why == "unsupported_version"
    if why == "bad_payload" then
      M.drop_pending(st)
    end
    return false, "the core refused the message: " .. clip(why, 120), long
  end
  -- A note with ok: true is the core's to make; it is shown once, not on every reply.
  if type(reply.err) == "string" and reply.err ~= st.note then
    st.note = reply.err
    M.log(st, "info", "the core says: " .. clip(reply.err, 200))
  end
  local ack = reply.ack
  if mtype(ack) ~= "integer" then
    ack = st.transport.implicit_ack and seq or nil
  end
  if ack and ack >= 1 and ack <= seq then
    M.ack(st, ack)
  end
  local n = M.handle_commands(st, reply.cmds)
  local poll = reply.poll_s
  if mtype(poll) == "integer" and poll >= 1 and poll < st.cfg.cycle_s then
    return true, nil, false, poll
  end
  if n > 0 then
    return true, nil, false, 1 -- report the results soon
  end
  return true
end

-- One cycle: sample, send, take the reply. Never raises; returns true when the core answered.
function M.cycle(st)
  local t0 = now()
  if st.rediscover or t0 >= st.next_discover then
    local okd, derr = try(M.discover, st)
    if not okd then
      M.record_error(st, "discover", derr)
    end
  end
  local oks, serr = try(sample, st)
  if not oks then
    M.record_error(st, "cycle", serr)
  end
  st.seq = st.seq + 1
  local okb, body = pcall(M.encode_payload, st)
  if not okb then
    M.record_error(st, "cycle", "encode: " .. tostring(body))
    st.next_cycle = t0 + st.cfg.cycle_s
    return false
  end
  st.last_bytes = #body
  local okt, reply, terr = try(st.transport.exchange, st.transport, body, st.seq)
  if not okt then
    reply, terr = nil, "transport error: " .. tostring(reply)
  end
  body = nil
  local ok, err, long, poll
  if reply then
    ok, err, long, poll = M.handle_reply(st, reply, st.seq)
  else
    ok, err = false, terr
  end
  if ok then
    if st.failures > 0 then
      M.log(st, "info", "the core answers again after " .. st.failures .. " failed tries")
    end
    st.failures = 0
    st.sent = st.sent + 1
    st.next_cycle = t0 + (poll or st.cfg.cycle_s)
  else
    st.failures = st.failures + 1
    st.failed = st.failed + 1
    M.record_error(st, "transport", err)
    st.next_cycle = t0 + backoff(st, long)
  end
  st.cycles = st.cycles + 1
  if t0 >= st.next_heartbeat then
    st.next_heartbeat = t0 + 300
    M.log(st, "info", ("alive: %d cycles, %d sent, %d failed, %d bytes last, %d bytes free, %.2f s sampling")
      :format(st.cycles, st.sent, st.failed, st.last_bytes or 0, int(computer.freeMemory()) or 0, st.sample_s or 0))
  end
  return ok
end

---------------------------------------------------------------------------------------------
-- State, run and main

local function boot_id()
  local seed = floor((now() * 1000 + (os.clock() * 1e6)) % 2147483647)
  return ("%08x"):format((math.random(0, 0x7fffffff) + seed) % 0x7fffffff)
end

function M.new_state(cfg, opts)
  opts = opts or {}
  local st = {
    cfg = cfg,
    out = opts.out or print,
    boot = opts.boot or boot_id(),
    seq = 0,
    failures = 0,
    sent = 0,
    failed = 0,
    cycles = 0,
    errors = {},
    results = {},
    crafts = {},
    done = {},
    done_order = {},
    next_cycle = 0,
    next_items = 0,
    next_discover = 0,
    next_heartbeat = 0,
    rediscover = true,
    inv = { power = {}, machines = {}, counts = json.object(), by_addr = {}, skipped = 0 },
    logged = {},
    logged_n = 0,
  }
  load_state(st)
  return st
end

local function describe(inv)
  local lines = {}
  for _, e in ipairs(inv.power) do
    lines[#lines + 1] = ("  power    %-17s %s %s%s (%s)"):format(e.type, short(e.addr), tostring(e.name),
      e.xyz and (" at " .. table.concat(e.xyz, ",")) or "", e.kind)
  end
  for _, e in ipairs(inv.machines) do
    lines[#lines + 1] = ("  machine  %-17s %s %s%s"):format(e.type, short(e.addr), tostring(e.name),
      e.xyz and (" at " .. table.concat(e.xyz, ",")) or "")
  end
  if inv.me then
    lines[#lines + 1] = ("  ae2      %-17s %s"):format(inv.me.type, short(inv.me.addr))
  end
  if inv.chat then
    lines[#lines + 1] = ("  chat     %-17s %s"):format("chat_box", short(inv.chat))
  end
  if inv.tps then
    lines[#lines + 1] = ("  tps      %-17s %s"):format("tps_card", short(inv.tps))
  end
  local types = {}
  for t, n in pairs(inv.counts) do
    types[#types + 1] = n > 1 and (t .. " x" .. n) or t
  end
  table.sort(types)
  lines[#lines + 1] = "  all: " .. table.concat(types, ", ")
  return lines
end

-- Results the core hasn't acknowledged yet, or a craft whose result AE2 hasn't given yet.
function M.waiting(st)
  if #st.results > 0 then
    return true
  end
  for _, c in ipairs(st.crafts) do
    if c.state == "computing" then
      return true
    end
  end
  return false
end

-- Run until Ctrl+C (the interrupted signal) or a hard interrupt. With opts.once: until the first
-- message is answered and the results of any commands it brought are reported (at most
-- ONCE_MAX_S), or an exchange fails.
function M.run(st, opts)
  opts = opts or {}
  local function on_interrupt()
    st.stop = true
  end
  local function on_component()
    st.rediscover = true
  end
  event.listen("interrupted", on_interrupt)
  event.listen("component_added", on_component)
  event.listen("component_removed", on_component)
  local ok, err = pcall(function()
    if not st.transport then
      local t, terr = M.open_transport(st)
      if not t then
        error({ fatal = terr }, 0)
      end
      st.transport = t
    end
    M.log(st, "info", ("gos-agent %s started (boot %s, %s, every %s s)"):format(VERSION, st.boot, st.transport.name, st.cfg.cycle_s))
    local once_until = now() + ONCE_MAX_S
    while not st.stop do
      if now() >= st.next_cycle then
        M.cycle(st)
        if opts.once and (st.failures > 0 or not M.waiting(st) or now() >= once_until) then
          break
        end
      end
      local wait = st.next_cycle - now()
      if not st.stop then
        if wait > 0 then
          event.pull(wait)
        else
          os.sleep(0) -- always yield between cycles
        end
      end
    end
  end)
  event.ignore("interrupted", on_interrupt)
  event.ignore("component_added", on_component)
  event.ignore("component_removed", on_component)
  if st.transport then
    pcall(st.transport.close, st.transport)
  end
  if not ok then
    if type(err) == "table" and err.fatal then
      M.log(st, "error", err.fatal)
      return false, err.fatal
    end
    if err == "interrupted" then
      M.log(st, "info", "stopped (interrupted)")
      return true
    end
    M.log(st, "error", "stopped: " .. tostring(err))
    return false, tostring(err)
  end
  M.log(st, "info", "stopped")
  return true
end

function M.main(argv, opts)
  opts = opts or {}
  local out = opts.out or print
  local mode, path
  for i = 1, argv.n or #argv do
    local a = argv[i]
    if a == "--list" or a == "--once" then
      mode = a
    elseif type(a) == "string" and a:sub(1, 2) == "--" then
      out("usage: gos-agent [--list | --once] [config]")
      return 2
    else
      path = a
    end
  end
  path = path or (DIR .. "/gos-agent.cfg")
  local cfg, err = M.load_config(path)
  if not cfg then
    out("gos-agent: " .. err)
    return 1
  end
  local st = M.new_state(cfg, { out = out, boot = opts.boot })
  if mode == "--list" then
    st.cfg.log_path = nil
    local inv = M.discover(st)
    out(("gos-agent %s: %d power, %d machines%s"):format(VERSION, #inv.power, #inv.machines, inv.me and ", AE2" or ""))
    for _, line in ipairs(describe(inv)) do
      out(line)
    end
    return 0
  end
  local ok, rerr = M.run(st, { once = mode == "--once" })
  if not ok then
    out("gos-agent: " .. tostring(rerr))
    return 1
  end
  if mode == "--once" then
    if st.failures == 0 then
      out(("sent %d bytes; the core answered"):format(st.last_bytes or 0))
      if st.sent > 1 then
        out(("  %d messages: the reply carried commands, and their results went in the later ones"):format(st.sent))
      end
      if M.waiting(st) then
        out(("  a result was still waiting after %d s: a craft AE2 is still planning is reported as failed"):format(ONCE_MAX_S)
          .. " (restarted before AE2 answered) by the next run")
      end
      return 0
    end
    local last = st.errors[#st.errors]
    out(("sent %d bytes; failed: %s"):format(st.last_bytes or 0, last and last.msg or "?"))
    return 1
  end
  return 0
end

if args[1] == "--lib" then
  return M
end
return M.main(args)
