--[[ alert_pipe.lua: the real OC agent in a fake OC world, posting to the real core over a pipe
(WO-7.5 acceptance; driven by tests/oc/test_alert_pipeline.py).

  lua5.3 tests/oc/alert_pipe.lua <token>

Every POST /oc/telemetry goes out on stdout as one JSON line, {op = "http", t = <virtual s>,
body = <the telemetry>}, and the Python side answers on stdin with {body = <the reply>}: the
core's own route, or, while the Python side plays a paused game, a bare reply that never reached
the core. The world (fakeoc's virtual clock, a 50 x 16 board, the standard base with an LSC at
50 %) turns bad at BAD_AT (the LSC at 10 %, the EBF stalled with 2 maintenance problems) and good
again at GOOD_AT (inside the Python side's pause). A watcher records when the board first shows
the power-low and crafting-done lines, and the last line is {op = "done", ...}.
]]

local script = arg and arg[0] or "tests/oc/alert_pipe.lua"
local root = script:match("^(.*)/tests/oc/alert_pipe%.lua$") or "."
package.path = root .. "/tests/oc/?.lua;" .. root .. "/oc/?.lua;" .. package.path

local F = require("fakeoc")
F.set_root(root)
local T = require("testlib")
local json = require("json")

T.TOKEN = assert(arg[1], "the core's OC token")

local BAD_AT, GOOD_AT, END_AT = 20, 110, 240

local w = T.base({ screen = { w = 50, h = 16 }, extra = "  sensor_s = 15,\n" })
w.lsc.stored_str, w.lsc.capacity_str = "500", "1000"

local function ask(op, body)
  io.write(json.encode({ op = op, t = w.clock, body = body }), "\n")
  io.flush()
  local line = assert(io.read("l"), "the core side closed the pipe")
  return assert(json.decode(line))
end

w.server = function(req)
  local r = ask("http", req.body)
  return { body = r.body }
end

local seen = {}
local function board_has(text)
  for y = 1, w.screen.res_h do
    local row = w.screen.rows[y]
    if row and row:find(text, 1, true) then
      return true
    end
  end
  return false
end
local function watch(world)
  local now = world.clock
  if not seen.board_low and board_has("LOW  ") then
    seen.board_low = now
  end
  if not seen.board_done and board_has("DONE ") then
    seen.board_done = now
  end
  world:at(now + 0.05, watch)
end
w:at(w.clock, watch)
w:at(BAD_AT, function()
  w.lsc.stored_str = "100"
  w.ebf.active = false
  w.ebf.sensor[5] = "Problems: \194\167c2"
end)
w:at(GOOD_AT, function()
  w.lsc.stored_str = "800"
  w.ebf.active = true
  w.ebf.sensor[5] = "Problems: \194\167c0"
end)

local M, st = T.agent(w)
T.run_until(M, st, w, END_AT)
seen.op = "done"
seen.bad_at, seen.good_at = BAD_AT, GOOD_AT
io.write(json.encode(seen), "\n")
io.flush()
