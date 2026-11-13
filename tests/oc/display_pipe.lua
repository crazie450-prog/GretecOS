--[[ display_pipe.lua: the real OC agent in a fake OC world, wired to the real core over a pipe
(WO-7.4 acceptance; driven by tests/oc/test_display_pipeline.py).

  lua5.3 tests/oc/display_pipe.lua http|mailbox <token>

Every exchange goes out on stdout as one JSON line, {op = "http"|"mailbox", t = <virtual s>,
body = <the telemetry the agent wrote>}, and the Python side answers on stdin with {body = <the
core's reply>, or null}: over http it is the core's own POST /oc/telemetry, over the mailbox the
core's own Mailbox.poll on a real folder. The world (fakeoc's virtual clock, a tier-1 screen and
GPU, a glasses terminal over http, a chat box, the standard base) changes on a schedule: the EBF
stalls at 33.3 s and runs again at 61.7 s. A watcher records when the board and the HUD first
show each change and what the chat box said, and the last line is {op = "done", ...}.
]]

local script = arg and arg[0] or "tests/oc/display_pipe.lua"
local root = script:match("^(.*)/tests/oc/display_pipe%.lua$") or "."
package.path = root .. "/tests/oc/?.lua;" .. root .. "/oc/?.lua;" .. package.path

local F = require("fakeoc")
F.set_root(root)
local T = require("testlib")
local json = require("json")

local transport, token = arg[1], arg[2]
assert(transport == "http" or transport == "mailbox", "usage: display_pipe.lua http|mailbox <token>")
T.TOKEN = assert(token, "the core's OC token")

local DIR = "/home/gos/mailbox"
local OUTBOX, TMP, INBOX = DIR .. "/outbox.json", DIR .. "/outbox.tmp", DIR .. "/inbox.json"
local STALL_AT, BACK_AT, END_AT = 33.3, 61.7, 120

local w = T.base({
  screen = { w = 50, h = 16 },
  glasses = true,
  no_internet = transport == "mailbox",
  extra = transport == "mailbox" and 'transport = "mailbox",' or nil,
})

local function ask(op, body)
  io.write(json.encode({ op = op, t = w.clock, body = body }), "\n")
  io.flush()
  local line = assert(io.read("l"), "the core side closed the pipe")
  return assert(json.decode(line))
end

if transport == "http" then
  w.server = function(req)
    local r = ask("http", req.body)
    return { body = r.body }
  end
else
  local function poll(world)
    local body = world.files[OUTBOX]
    if body and not world.files[INBOX] then
      world.files[TMP], world.files[OUTBOX] = body, nil
      local r = ask("mailbox", body)
      if r.body ~= json.null and r.body ~= nil then
        world.files[INBOX] = r.body
        world.mtimes[INBOX] = world.clock * 1000
      end
    end
    world:at(world.clock + 0.25, poll)
  end
  w:at(w.clock + 0.25, poll)
end

local seen = { said = {} }
local function board_has(text)
  for y = 1, w.screen.res_h do
    local row = w.screen.rows[y]
    if row and row:find(text, 1, true) then
      return true
    end
  end
  return false
end
local function hud_has(text)
  for _, wd in pairs(w.glasses.widgets) do
    if wd.text:find(text, 1, true) then
      return true
    end
  end
  return false
end
local said = 0
local function watch(world)
  local now = world.clock
  if now >= STALL_AT and not seen.board_stalled and board_has("stalled") then
    seen.board_stalled = now
  end
  if now >= STALL_AT and not seen.hud_stalled and hud_has("stalled") then
    seen.hud_stalled = now
  end
  if now >= BACK_AT and not seen.board_back and not board_has("stalled") and board_has("no problems") then
    seen.board_back = now
  end
  while said < #w.chat.said do
    said = said + 1
    seen.said[#seen.said + 1] = { t = now, text = w.chat.said[said] }
  end
  world:at(now + 0.05, watch)
end
w:at(w.clock, watch)
w:at(STALL_AT, function()
  w.ebf.active = false
end)
w:at(BACK_AT, function()
  w.ebf.active = true
end)
w:at(END_AT - 1, function(world)
  seen.rows = {}
  for y = 1, world.screen.res_h do
    seen.rows[y] = world.screen.rows[y] or ""
  end
  seen.hud = {}
  for id, wd in pairs(world.glasses.widgets) do
    seen.hud[id] = wd.text
  end
  seen.energy = world.glasses:energy_per_tick()
  seen.paused = w.ebf.allowed == false
end)

local M, st = T.agent(w)
T.run_until(M, st, w, END_AT)
seen.op = "done"
seen.stall_at, seen.back_at = STALL_AT, BACK_AT
seen.width, seen.height = w.screen.res_w, w.screen.res_h
seen.overflow = w.screen.overflow or 0
io.write(json.encode(seen), "\n")
io.flush()
