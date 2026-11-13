--[[ testlib.lua: assertions and a standard test base for the OC agent's Lua tests (WO-7.2). ]]

local F = require("fakeoc")
local json = require("json")

local T = { dump_dir = nil, current = "?" }

local function fmt(v)
  if type(v) == "string" then
    return string.format("%q", v)
  end
  if type(v) == "table" then
    local ok, s = pcall(json.encode, v)
    if ok then
      return s
    end
  end
  return tostring(v)
end

function T.fail(msg, level)
  error(msg, (level or 1) + 1)
end

function T.eq(got, want, what)
  if got ~= want then
    T.fail(("%s: got %s, want %s"):format(what or "value", fmt(got), fmt(want)), 2)
  end
end

function T.ok(v, what)
  if not v then
    T.fail((what or "expected a true value") .. " (got " .. fmt(v) .. ")", 2)
  end
  return v
end

function T.no(v, what)
  if v then
    T.fail((what or "expected a false value") .. " (got " .. fmt(v) .. ")", 2)
  end
end

function T.has(s, sub, what)
  if type(s) ~= "string" or not s:find(sub, 1, true) then
    T.fail(("%s: %s does not contain %s"):format(what or "text", fmt(s), fmt(sub)), 2)
  end
end

function T.near(got, want, tol, what)
  if type(got) ~= "number" or math.abs(got - want) > tol then
    T.fail(("%s: got %s, want %s +- %s"):format(what or "number", fmt(got), fmt(want), tol), 2)
  end
end

function T.between(got, lo, hi, what)
  if type(got) ~= "number" or got < lo or got > hi then
    T.fail(("%s: got %s, want %s..%s"):format(what or "number", fmt(got), lo, hi), 2)
  end
end

local function deep_eq(a, b)
  if a == b then
    return true
  end
  if type(a) ~= "table" or type(b) ~= "table" then
    return false
  end
  for k, v in pairs(a) do
    if not deep_eq(v, b[k]) then
      return false
    end
  end
  for k in pairs(b) do
    if a[k] == nil then
      return false
    end
  end
  return true
end
T.deep_eq = deep_eq

function T.same(got, want, what)
  if not deep_eq(got, want) then
    T.fail(("%s: got %s, want %s"):format(what or "table", fmt(got), fmt(want)), 2)
  end
end

function T.raises(fn, sub, what)
  local ok, err = pcall(fn)
  if ok then
    T.fail((what or "call") .. " did not raise", 2)
  end
  if sub and not tostring(err):find(sub, 1, true) then
    T.fail(("%s raised %s, want %s"):format(what or "call", fmt(tostring(err)), fmt(sub)), 2)
  end
end

-- Write a file into the dump folder (the Python test validates these against the schema).
function T.dump(name, text)
  if not T.dump_dir then
    return
  end
  local f = assert(io.open(T.dump_dir .. "/" .. name, "wb"))
  f:write(text)
  f:close()
end

---------------------------------------------------------------------------------------------
-- The standard base: the computer's own parts, an internet card, one EBF, one battery buffer, one
-- LSC, an ME controller with a watchlist's items and two patterns, a chat box and a tps card.

T.TOKEN = "test-token-0123456789"

T.WATCH = [[
  watch = {
    { name = "minecraft:iron_ingot", damage = 0 },
    { name = "gregtech:gt.metaitem.01", damage = 11305 },
    { name = "gregtech:gt.metaitem.01", damage = 2305 },
    { name = "minecraft:diamond", damage = 0 },
  },
]]

function T.config_text(extra)
  return "-- test config\n{\n  token = \"" .. T.TOKEN .. "\",\n" .. T.WATCH .. (extra or "") .. "\n}\n"
end

function T.base(opts)
  opts = opts or {}
  F.reset_addresses()
  local w = F.world(opts)
  if T.on_world then
    T.on_world(w) -- the runner's yield watchdog follows this world's clock
  end
  F.plain(w, "computer", { prefix = "0c" })
  F.plain(w, "eeprom", { prefix = "0e" })
  w.boot_fs = F.plain(w, "filesystem", { prefix = "0f" }).addr -- the managed drive OpenOS boots from
  if opts.screen then -- a working screen and GPU (WO-7.4's board): { w = , h = } the screen's tier
    w.screen = F.screen(w, { prefix = "05", w = opts.screen.w, h = opts.screen.h })
    w.gpu = F.gpu(w, { prefix = "06", w = opts.screen.gpu_w, h = opts.screen.gpu_h })
  else
    F.plain(w, "screen", { prefix = "05" })
    F.plain(w, "gpu", { prefix = "06" })
  end
  if opts.glasses then
    w.glasses = F.glasses(w, { prefix = "08" })
  end
  F.plain(w, "keyboard", { prefix = "07" })
  if not opts.no_internet then
    w.card = F.internet_card(w, { prefix = "1a" })
  end
  if not opts.bare then
    w.ebf = F.gt_machine(w, { prefix = "2e", name = "multimachine.blastfurnace", xyz = { 120, 64, -35 } })
    w.buffer = F.battery_buffer(w, { prefix = "3b", xyz = { 118, 64, -30 } })
    w.lsc = F.lsc(w, { prefix = "4c", xyz = { 110, 60, -40 } })
    w.me = F.me(w, {
      prefix = "5e",
      items = {
        { name = "minecraft:iron_ingot", damage = 0, size = 1234, isCraftable = true },
        { name = "gregtech:gt.metaitem.01", damage = 11305, size = 640, isCraftable = true },
        { name = "gregtech:gt.metaitem.01", damage = 11305, size = 3, hasTag = true },
        { name = "gregtech:gt.metaitem.01", damage = 2032, size = 99 },
        { name = "gregtech:gt.metaitem.01", damage = 11032, size = 12 },
        { name = "minecraft:cobblestone", damage = 0, size = 100000 },
      },
      patterns = {
        ["gregtech:gt.metaitem.01@11305"] = { plan_ticks = 3, craft_ticks = 100 },
        ["minecraft:iron_ingot@0"] = { plan_ticks = 2, fail = "missing resources" },
      },
    })
    w.chat = F.chat_box(w, { prefix = "6c" })
    w.tps = F.tps_card(w, { prefix = "7a" })
  end
  if not opts.no_config then
    F.write_config(w, opts.config or T.config_text(opts.extra))
  end
  w.cmds_queue = {}
  w.valid_replies = {}
  -- The default core: 200 with the queued commands, acking every message. Its replies are
  -- valid by the schema unless a test queues a bad command on purpose (it then sets
  -- w.bad_cmds), so the runner dumps them for the Python check.
  w.server = opts.server or function(req, world)
    local cmds = world.cmds_queue
    world.cmds_queue = {}
    local reply = { v = 1, ok = true, cmds = json.array(cmds) }
    local body = json.encode(reply)
    if not world.bad_cmds and not world.lean then
      world.valid_replies[#world.valid_replies + 1] = body
    end
    return { body = body }
  end
  return w
end

-- Load the agent into the world, with its config, ready to cycle.
function T.agent(w, opts)
  opts = opts or {}
  local M = assert(F.load_agent(w, opts))
  local cfg = assert(M.load_config(opts.config_path or "/home/gos/gos-agent.cfg"))
  local st = M.new_state(cfg, { out = function(...) w:print(...) end, boot = opts.boot or "b0075eed" })
  if not opts.no_transport then
    st.transport = assert(M.open_transport(st))
  end
  return M, st
end

-- Decoded bodies of the HTTP requests the agent made.
function T.bodies(w)
  local out = {}
  for i, r in ipairs(w.requests) do
    out[i] = assert(json.decode(r.body))
  end
  return out
end

function T.last_body(w)
  local r = w.requests[#w.requests]
  return r and assert(json.decode(r.body)) or nil
end

-- Run the agent's main loop until virtual time `until_t` (a hard stop through a fake interrupt).
function T.run_until(M, st, w, until_t, opts)
  w.hard_interrupt_at = until_t
  local ok, err = M.run(st, opts)
  return ok, err
end

function T.cmd(id, kind, args, via)
  return { id = id, kind = kind, args = json.object(args or {}), confirmed_via = via == nil and "voice" or via }
end

return T
