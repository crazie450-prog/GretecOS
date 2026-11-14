--[[ station_pipe.lua: the real OC agent with its breeding station in a fake OC world, wired to the
real core over a pipe (WO-7.6 acceptance; driven by tests/oc/test_station_pipeline.py).

  lua5.3 tests/oc/station_pipe.lua <token>

Every exchange goes out on stdout as one JSON line, {op = "http", t = <virtual s>, body = <the
telemetry>}, and the Python side answers on stdin with {body = <the core's reply from its own
POST /oc/telemetry>}. The world: the standard base plus fakeoc's station (a chest with starter
seeds of Wheat, Reed, Melon and Pumpkin and bone meal, an MV Crop Breeder whose runs fail as
injected, three Seed Generators) on one transposer. The last line is {op = "done", ...}: when the
station first moved an item, what the chest holds, every breeder load's chest count after it, and
what is left in the machines.
]]

local script = arg and arg[0] or "tests/oc/station_pipe.lua"
local root = script:match("^(.*)/tests/oc/station_pipe%.lua$") or "."
package.path = root .. "/tests/oc/?.lua;" .. root .. "/oc/?.lua;" .. package.path

local F = require("fakeoc")
F.set_root(root)
local T = require("testlib")
local json = require("json")

T.TOKEN = assert(arg[1], "the core's OC token")
local END_AT = tonumber(arg[2]) or 4000

local w = T.base({
  extra = 'crops = { storage = 0, breeder = 1, generators = { { side = 2, adapter = "9e1" }, '
    .. '{ side = 3, adapter = "9e2" }, { side = 4, adapter = "9e3" } } },\n',
})
local st = F.station(w, {
  gens = 3,
  stock = {
    F.seed("Wheat Seeds", 8),
    F.seed("Reed Seeds", 8),
    F.seed("Melon Seeds", 8),
    F.seed("Pumpkin Seeds", 8),
    F.item("minecraft:dye", 15, 16, "Bone Meal"),
  },
  breeder = {
    recipes = {
      { parents = { "Wheat Seeds", "Reed Seeds" }, child = "Carrot Seeds" },
      { parents = { "Carrot Seeds", "Melon Seeds" }, child = "Potato Seeds" },
      { parents = { "Potato Seeds", "Pumpkin Seeds" }, child = "Tomato Seeds", cat = { { name = "minecraft:dye", damage = 15, n = 1 } } },
    },
    -- failures injected: four of the first seven runs fail, then one in three
    outcomes = { false, true, false, false, true, false, true, true, false, true, true, false, true, true, false, true },
    duration = 12,
  },
})

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

local M, ast = T.agent(w)
-- The station's end (done or stopped) ends the run a little later, so its last report goes out.
local function watch(world)
  local s = ast.station
  if s and (s.phase == "done" or s.phase == "stopped") and not world.ended_at then
    world.ended_at = world.clock
    world.hard_interrupt_at = world.clock + 12
  end
  world:at(world.clock + 1, watch)
end
w:at(w.clock + 1, watch)
T.run_until(M, ast, w, END_AT)
if w.ended_at and w.clock < END_AT then
  -- run_until stopped at the earlier interrupt: nothing more to do
end

local loads = {}
for _, mv in ipairs(w.crop_moves or {}) do
  if mv.dst == 1 and mv.name == F.SEED then
    loads[#loads + 1] = { label = mv.label, left = mv.left, n = mv.n }
  end
end
local inside = {}
local function machine(m)
  local out = {}
  for i = m.first, m.out do
    if m.slots[i] then
      out[#out + 1] = { slot = i, label = m.slots[i].label, size = m.slots[i].size }
    end
  end
  return { slots = out, running = m.run ~= nil }
end
inside.breeder = machine(st.breeder)
for i, g in ipairs(st.gens) do
  inside["gen" .. i] = machine(g)
end
local seen = {
  op = "done",
  phase = ast.station and ast.station.phase,
  note = ast.station and ast.station.note or json.null,
  stock = ast.station and ast.station.stock or json.null,
  first_move = w.crop_moves and w.crop_moves[1] and w.crop_moves[1].t or json.null,
  runs = #st.breeder.results,
  tomato = F.count(st.chest, "Tomato Seeds", F.SEED),
  loads = loads,
  inside = inside,
  clock = w.clock,
}
io.write(json.encode(seen), "\n")
io.flush()
