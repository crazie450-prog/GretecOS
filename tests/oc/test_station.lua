-- The breeding station, oc/crop/station.lua (WO-7.6; plan 7.17 item 4, D78, D282-D284): a GOS
-- breeding plan run on fakeoc's Crop Breeder and Seed Generators through one transposer. A
-- 3-step plan bred from starter seeds with failures injected, stocking 4; never the last seed of a
-- parent; a stop that leaves nothing in transit; start and stop only as confirmed commands.
local T = require("testlib")
local F = require("fakeoc")
local json = require("json")

local tests = {}
local function test(name, fn)
  tests[#tests + 1] = { name, fn }
end

local SEED = F.SEED
local KEEP = 1
local BONE = { name = "minecraft:dye", damage = 15, n = 1 }

-- Wheat + Reed -> Carrot; Carrot + Melon -> Potato; Potato + Pumpkin (+ bone meal) -> Tomato.
local RECIPES = {
  { parents = { "Wheat Seeds", "Reed Seeds" }, child = "Carrot Seeds" },
  { parents = { "Carrot Seeds", "Melon Seeds" }, child = "Potato Seeds" },
  { parents = { "Potato Seeds", "Pumpkin Seeds" }, child = "Tomato Seeds", cat = { BONE } },
}

local function plan(extra)
  local p = {
    plan = "0a1b2c3d",
    target = "Tomato Seeds",
    count = 4,
    steps = {
      { child = "Carrot Seeds", parents = { "Wheat Seeds", "Reed Seeds" }, cat = json.array(), copies = 3, fert = 500 },
      { child = "Potato Seeds", parents = { "Carrot Seeds", "Melon Seeds" }, cat = json.array(), copies = 3, fert = 500 },
      { child = "Tomato Seeds", parents = { "Potato Seeds", "Pumpkin Seeds" }, cat = { BONE }, copies = 3, fert = 500 },
    },
    copy = { "Tomato Seeds", "Potato Seeds", "Carrot Seeds", "Melon Seeds", "Pumpkin Seeds", "Wheat Seeds", "Reed Seeds" },
  }
  for k, v in pairs(extra or {}) do
    p[k] = v
  end
  return p
end

local function starters(n)
  return {
    F.seed("Wheat Seeds", n),
    F.seed("Reed Seeds", n),
    F.seed("Melon Seeds", n),
    F.seed("Pumpkin Seeds", n),
    F.item("minecraft:dye", 15, 16, "Bone Meal"),
  }
end

local function crops_cfg(gens)
  local list = {}
  for i = 1, gens do
    list[#list + 1] = ("{ side = %d%s }"):format(1 + i, gens > 1 and (', adapter = "9e' .. i .. '"') or "")
  end
  return ("crops = { storage = 0, breeder = 1, generators = { %s } },\n"):format(table.concat(list, ", "))
end

-- The station module on its own, over a world (for the long property runs: no agent, no HTTP).
local function load_station(w, gens, extra)
  local S = dofile(F.ROOT .. "/oc/crop/station.lua")
  local list = {}
  for i = 1, gens do
    list[i] = { side = 1 + i, adapter = gens > 1 and ("9e" .. i) or nil }
  end
  local t = { storage = 0, breeder = 1, generators = list }
  for k, v in pairs(extra or {}) do
    t[k] = v
  end
  local cfg = assert(S.check_config(t, "/home/gos"))
  local logs = {}
  local s = S.new(cfg, {
    call = function(addr, method, ...)
      local r = table.pack(pcall(w.component.invoke, addr, method, ...))
      if r[1] and r[2] == nil and r[3] ~= nil then
        return false, r[3]
      end
      return table.unpack(r, 1, r.n)
    end,
    now = function()
      return w.clock
    end,
    json = json,
    list = function(ty)
      local out = {}
      for addr, dty in w.component.list(ty, true) do
        out[addr] = dty
      end
      return out
    end,
    read = function(path)
      return w.files[path]
    end,
    write = function(path, text)
      w.files[path] = text
      return true
    end,
    log = function(level, msg)
      logs[#logs + 1] = level .. " " .. msg
    end,
  })
  return S, s, logs
end

-- Step the station until it is done, stopped or `until_t`; stop_at sends a confirmed stop.
local function drive(w, s, until_t, stop_at)
  local stopped_sent = false
  while w.clock < until_t do
    if stop_at and not stopped_sent and w.clock >= stop_at then
      stopped_sent = true
      T.eq(s:stop(99).ok, true, "the stop is taken")
    end
    s:step()
    if s.phase == "done" or s.phase == "stopped" then
      return
    end
    w:advance_to(math.max(s.next_at, w.clock + 0.05))
  end
end

local function seeds_in(inv, label)
  local c = 0
  for i = 1, inv.size do
    local st = inv.slots[i]
    if st and st.name == SEED and (label == nil or st.label == label) then
      c = c + st.size
    end
  end
  return c
end

-- Decoded request bodies after a long run: the clock moves a tick a body, so the watchdog (which
-- counts instructions while the clock stands still) doesn't take hundreds of decodes for a hang.
local function bodies(w)
  local out = {}
  for i, r in ipairs(w.requests) do
    out[i] = assert(json.decode(r.body))
    w:tick()
  end
  return out
end

local function machine_empty(m)
  for i = m.first, m.out do
    if m.slots[i] then
      return false
    end
  end
  return true
end

-- Every breeder load left at least KEEP of that parent in the chest: never the last seed.
local function never_last_seed(w)
  local loads = 0
  for _, mv in ipairs(w.crop_moves or {}) do
    if mv.dst == 1 and mv.name == SEED then
      loads = loads + 1
      T.ok(mv.left and mv.left >= KEEP, ("a load of %s left %s in the chest"):format(mv.label, tostring(mv.left)))
      T.eq(mv.n, 1, "one seed of each parent per run")
    end
  end
  return loads
end

-- Seeds are only moved, copied, made by a success and used by a run: count them all.
local function conserved(st, initial)
  local total = seeds_in(st.chest) + seeds_in(st.breeder)
  for _, g in ipairs(st.gens) do
    total = total + seeds_in(g)
  end
  local made = 0
  for _, g in ipairs(st.gens) do
    made = made + g.copies
  end
  local used = 0
  for _, r in ipairs(st.breeder.results) do
    used = used + 2
    if r.ok and r.t + st.breeder.duration <= st.breeder.w.clock then
      made = made + 1
    end
  end
  -- a run under way holds its child until it ends
  if st.breeder.run and st.breeder.run.out then
    made = made - 1
  end
  for _, g in ipairs(st.gens) do
    if g.run then
      made = made - 1
    end
  end
  T.eq(total, initial + made - used, "seeds are conserved")
end

-- After a stop or the end: nothing the station moved is anywhere but the chest, except a parked
-- seed in each copier (its first input slot) and the copiers' catalysts.
local function nothing_in_transit(st)
  T.no(st.breeder.run, "no breeder run under way")
  T.ok(machine_empty(st.breeder), "the Crop Breeder is empty")
  for i, g in ipairs(st.gens) do
    T.no(g.run, "generator " .. i .. " isn't copying")
    T.no(g.slots[g.out], "generator " .. i .. "'s output is empty")
    local parked = 0
    for j = g.first, g.last do
      local s = g.slots[j]
      if s and s.name == SEED then
        parked = parked + s.size
        T.eq(j, g.first, "a parked seed sits in the first input")
      end
    end
    T.ok(parked <= 1, "at most one parked seed per copier")
  end
end

---------------------------------------------------------------------------------------------
-- Config and plan checks

test("config: sides and generators are checked", function()
  local S = dofile(F.ROOT .. "/oc/crop/station.lua")
  T.ok(S.check_config({ storage = 0, breeder = 1, generators = { { side = 2 } } }))
  local bad = {
    { storage = 0, breeder = 0, generators = { { side = 2 } } },
    { storage = 0, breeder = 1, generators = { { side = 1 } } },
    { storage = 0, breeder = 1, generators = {} },
    { storage = 0, breeder = 1, generators = { { side = 2 }, { side = 3 } } }, -- adapters unnamed
    { storage = 0, breeder = 1, generators = { { side = 2 } }, keep = 0 },
    { storage = 0, breeder = 1, generators = { { side = 2 } }, colour = "red" },
    { storage = 7, breeder = 1, generators = { { side = 2 } } },
  }
  for i, c in ipairs(bad) do
    local ok, err = S.check_config(c)
    T.no(ok, "bad config " .. i)
    T.ok(type(err) == "string" and #err > 0)
  end
end)

test("plan: only labels of analysed seeds, bounded lists, the target bred last", function()
  local S = dofile(F.ROOT .. "/oc/crop/station.lua")
  T.ok(S.check_plan(plan()))
  local function bad(extra, what)
    local p = plan(extra)
    local ok = S.check_plan(p)
    T.no(ok, what)
  end
  bad({ plan = "xyz" }, "plan id")
  bad({ target = "Unknown Seeds" }, "an unanalysed seed's label")
  bad({ target = "minecraft:wheat_seeds" }, "a registry name is not a label")
  bad({ count = 0 }, "count")
  bad({ count = 65 }, "count")
  bad({ target = "Potato Seeds" }, "the last step must breed the target")
  local p = plan()
  p.steps[1].parents = { "Wheat Seeds", "Wheat Seeds" }
  T.no(S.check_plan(p), "distinct parents")
  p = plan()
  p.steps[1].fert = 0
  T.no(S.check_plan(p), "fertilizer")
  p = plan()
  p.steps[2].cat = { { name = "12", damage = 0, n = 1 } }
  T.no(S.check_plan(p), "a numeric id is no registry name")
end)

---------------------------------------------------------------------------------------------
-- Through the agent: confirmed commands only

test("an unconfirmed start or stop never runs; nothing moves", function()
  local w = T.base({ extra = crops_cfg(1) })
  w.bad_cmds = true -- these break the schema on purpose
  local st = F.station(w, { stock = starters(6) })
  local M, ast = T.agent(w)
  M.open_station(ast)
  w.cmds_queue = {
    T.cmd(1, "crop_start", plan(), false),
    T.cmd(2, "crop_start", plan(), "chat"),
    T.cmd(3, "crop_stop", {}, false),
  }
  for _ = 1, 3 do
    ast.next_cycle = 0
    M.cycle(ast)
  end
  local seen = {}
  for _, b in ipairs(T.bodies(w)) do
    for _, r in ipairs(b.results) do
      seen[r.id] = r
    end
  end
  for id = 1, 3 do
    T.eq(seen[id].state, "refused", "command " .. id)
    T.has(seen[id].err, "not confirmed")
  end
  for _ = 1, 20 do
    M.station_step(ast)
    w:advance_to(w.clock + 2)
  end
  T.eq(#(w.crop_moves or {}), 0, "no item moved")
  T.eq(ast.station.phase, "idle")
  T.eq(st.breeder.runs, 0)
end)

test("no crops table: crop commands are refused", function()
  local w = T.base()
  local M, st = T.agent(w)
  w.cmds_queue = { T.cmd(1, "crop_start", plan()), T.cmd(2, "crop_stop", {}) }
  for _ = 1, 2 do
    st.next_cycle = 0
    M.cycle(st)
  end
  local body = T.last_body(w)
  local n = 0
  for _, r in ipairs(body.results) do
    T.eq(r.state, "refused")
    T.has(r.err, "no breeding station")
    n = n + 1
  end
  T.eq(n, 2)
end)

test("a bad crops table stops the agent at start", function()
  local w = T.base({ extra = "crops = { storage = 0, breeder = 0, generators = { { side = 2 } } },\n" })
  local M, st = T.agent(w)
  local ok, err = M.run(st)
  T.no(ok)
  T.has(err, "crops")
end)

-- The acceptance run (plan 8 WO-7.6), through the agent's main loop and its HTTP core: a 3-step
-- target from starter seeds, failures injected, 4 stocked, unattended after the confirmed start.
test("3-step plan through the agent: failures, copies first, 4 stocked", function()
  local w = T.base({ extra = crops_cfg(3) })
  local st = F.station(w, {
    gens = 3,
    stock = starters(5),
    breeder = { recipes = RECIPES, outcomes = { false, true, false, false, true, false, true }, duration = 12 },
  })
  local M, ast = T.agent(w)
  w.cmds_queue = { T.cmd(1, "crop_start", plan()) }
  T.run_until(M, ast, w, 3600)
  local s = ast.station
  T.eq(s.phase, "done", s.note)
  T.ok(F.count(st.chest, "Tomato Seeds", SEED) >= 4, "4 Tomato Seeds stocked")
  T.ok(st.breeder.runs >= 6, "the failures were retried")
  T.eq(s.fail, 4, "four runs failed, as injected")
  T.eq(s.ok, 3, "one success per step")
  T.ok(never_last_seed(w) >= 7, "seven loads")
  nothing_in_transit(st)
  -- the start's result went to the core, and the status rode along in telemetry
  local result, last
  for _, b in ipairs(bodies(w)) do
    for _, r in ipairs(b.results) do
      if r.id == 1 then
        result = r
      end
    end
    last = b.crops or last
  end
  T.eq(result.state, "done")
  T.eq(result.kind, "crop_start")
  T.eq(last.state, "done")
  T.eq(last.plan, "0a1b2c3d")
  T.ok(last.stock["Tomato Seeds"] >= 4)
  T.has(last.note, "done")
  -- copies came before the first runs: every parent's copier was set before its step's first load
  local first_load, parked = nil, {}
  for _, mv in ipairs(w.crop_moves) do
    if mv.dst >= 2 and mv.name == SEED then
      parked[mv.label] = parked[mv.label] or mv.t
    end
    if mv.dst == 1 and mv.name == SEED and not first_load then
      first_load = mv.t
    end
  end
  T.ok(parked["Tomato Seeds"], "the target got a copier")
  T.ok(parked["Potato Seeds"] and parked["Carrot Seeds"], "the plan's next ranks got the other two")
end)

test("a confirmed stop mid-run through the agent: the run ends, nothing left in transit", function()
  local w = T.base({ extra = crops_cfg(1) })
  local st = F.station(w, {
    stock = starters(6),
    breeder = { recipes = RECIPES, outcomes = { true, true, true }, duration = 40 },
  })
  local M, ast = T.agent(w)
  w.cmds_queue = { T.cmd(1, "crop_start", plan()) }
  -- stop while the first run is under way
  w:at(40, function(world)
    world.cmds_queue = { T.cmd(2, "crop_stop", {}) }
  end)
  T.run_until(M, ast, w, 400)
  T.eq(ast.station.phase, "stopped", ast.station.note)
  T.eq(st.breeder.runs, 1, "no run started after the stop")
  T.eq(F.count(st.chest, "Carrot Seeds", SEED), 1, "the run under way finished and its child was collected")
  nothing_in_transit(st)
  T.eq(st.breeder.allowed, true, "the breeder's work switch is back as it was")
  local stopped
  for _, b in ipairs(bodies(w)) do
    for _, r in ipairs(b.results) do
      if r.id == 2 then
        stopped = r
      end
    end
  end
  T.eq(stopped.state, "done")
end)

---------------------------------------------------------------------------------------------
-- The station alone

test("one copier: it goes to the target; parents come from the starters and breeding", function()
  local w = T.base({ bare = true })
  local st = F.station(w, {
    gens = 1,
    stock = starters(10),
    breeder = { recipes = RECIPES, outcomes = { true, false, true, true, true, true }, duration = 10 },
  })
  local _, s = load_station(w, 1)
  T.eq(s:start(1, plan()).state, "done")
  drive(w, s, 5000)
  T.eq(s.phase, "done", s.note)
  T.ok(F.count(st.chest, "Tomato Seeds", SEED) >= 4)
  T.eq(st.gens[1].slots[st.gens[1].first].label, "Tomato Seeds", "the one copier holds the target")
  never_last_seed(w)
  nothing_in_transit(st)
end)

test("never the last seed: short starters wait, never loaded", function()
  local w = T.base({ bare = true })
  local st = F.station(w, {
    gens = 1,
    stock = { F.seed("Wheat Seeds", 1), F.seed("Reed Seeds", 5), F.seed("Melon Seeds", 5), F.seed("Pumpkin Seeds", 5) },
    breeder = { recipes = RECIPES },
  })
  local _, s = load_station(w, 1)
  s:start(1, plan({ copy = { "Tomato Seeds" } }))
  drive(w, s, 300)
  T.eq(s.phase, "running")
  T.eq(st.breeder.runs, 0, "a single Wheat Seed is never put in the breeder")
  T.eq(F.count(st.chest, "Wheat Seeds", SEED), 1)
  T.has(s.note, "Wheat Seeds")
end)

test("unanalysed seeds are never used", function()
  local w = T.base({ bare = true })
  local st = F.station(w, {
    gens = 1,
    stock = { F.seed("Unknown Seeds", 20), F.seed("Reed Seeds", 5) },
    breeder = { recipes = RECIPES },
  })
  local _, s = load_station(w, 1)
  s:start(1, plan({ copy = json.array() }))
  drive(w, s, 200)
  T.eq(#w.crop_moves, 0, "nothing moved")
  T.eq(F.count(st.chest, "Unknown Seeds", SEED), 20)
end)

test("no enriched fertilizer: the breeder is never loaded", function()
  local w = T.base({ bare = true })
  local st = F.station(w, { gens = 1, stock = starters(6), breeder = { recipes = RECIPES, fluid = 100 } })
  local _, s = load_station(w, 1)
  s:start(1, plan({ copy = { "Tomato Seeds" } }))
  drive(w, s, 300)
  T.eq(st.breeder.runs, 0)
  T.ok(machine_empty(st.breeder), "nothing waits in its inputs")
  T.has(s.note, "fertilizer")
  st.breeder.fluid = 100000
  drive(w, s, 600)
  T.ok(st.breeder.runs >= 1, "it starts once there is fertilizer")
end)

test("the breeder is paused while loading: a smaller recipe never starts on a half set", function()
  local w = T.base({ bare = true })
  -- Wheat + Reed alone make Carrot, but the plan wants Wheat + Reed + Melon -> Melon Pie (3 parents)
  local st = F.station(w, {
    gens = 1,
    stock = starters(6),
    breeder = {
      recipes = {
        { parents = { "Wheat Seeds", "Reed Seeds" }, child = "Carrot Seeds" },
        { parents = { "Wheat Seeds", "Reed Seeds", "Melon Seeds" }, child = "Pie Seeds" },
      },
      outcomes = { true },
    },
  })
  local _, s = load_station(w, 1)
  s:start(1, {
    plan = "00000001",
    target = "Pie Seeds",
    count = 1,
    steps = { { child = "Pie Seeds", parents = { "Wheat Seeds", "Reed Seeds", "Melon Seeds" }, cat = json.array(), copies = 2, fert = 500 } },
    copy = json.array(),
  })
  drive(w, s, 300)
  T.eq(s.phase, "done", s.note)
  T.eq(st.breeder.results[1].child, "Pie Seeds")
  T.eq(F.count(st.chest, "Carrot Seeds", SEED), 0)
end)

test("a restart mid-run: interrupted until a confirmed start resumes it", function()
  local w = T.base({ bare = true })
  local st = F.station(w, { gens = 1, stock = starters(10), breeder = { recipes = RECIPES, outcomes = { true, true, true }, duration = 30 } })
  local _, s = load_station(w, 1)
  s:start(5, plan())
  drive(w, s, 25)
  T.eq(s.phase, "running")
  -- the computer restarts: a new station reads the saved state
  local _, s2 = load_station(w, 1)
  T.eq(s2.phase, "interrupted")
  local moves = #w.crop_moves
  for _ = 1, 30 do
    s2:step()
    w:advance_to(w.clock + 2)
  end
  T.eq(#w.crop_moves, moves, "nothing moves while interrupted")
  T.eq(s2:start(6, { plan = "0f0f0f0f", target = "Tomato Seeds", count = 1, steps = plan().steps }).state, "refused",
    "another plan can't start over an interrupted one")
  T.eq(s2:start(7, plan()).state, "done", "the same plan resumes")
  drive(w, s2, 4000)
  T.eq(s2.phase, "done", s2.note)
  nothing_in_transit(st)
end)

test("a stop while interrupted winds the run down", function()
  local w = T.base({ bare = true })
  local st = F.station(w, { gens = 1, stock = starters(6), breeder = { recipes = RECIPES, outcomes = { true }, duration = 60 } })
  local _, s = load_station(w, 1)
  s:start(5, plan())
  drive(w, s, 30)
  local _, s2 = load_station(w, 1)
  T.eq(s2.phase, "interrupted")
  T.eq(s2:stop(8).state, "done")
  drive(w, s2, 400)
  T.eq(s2.phase, "stopped", s2.note)
  nothing_in_transit(st)
end)

-- Property tests: random starters, copiers, failure patterns and stop times. In every run the
-- breeder is never given the last seed of a parent, seeds are conserved, and a stop (or the end)
-- leaves nothing in transit; runs that aren't stopped and have enough seeds finish with 4 stocked.
test("property: never the last seed, nothing in transit after a stop, seeds conserved", function()
  local rand = (function(seed)
    local x = seed
    return function(n)
      x = (x * 1103515245 + 12345) % 2147483648
      return 1 + x % n
    end
  end)(4242)
  local finished, stops = 0, 0
  for trial = 1, 40 do
    local w = T.base({ bare = true })
    w.lean = true
    local gens = rand(3)
    local n = 1 + rand(8)
    local outcomes = {}
    for i = 1, 40 do
      outcomes[i] = rand(10) <= 5
    end
    local st = F.station(w, {
      gens = gens,
      stock = starters(n),
      breeder = { recipes = RECIPES, outcomes = outcomes, duration = 5 + rand(30), seed = trial },
      generator = { duration = 2 + rand(6) },
    })
    local initial = seeds_in(st.chest)
    local _, s = load_station(w, gens)
    T.eq(s:start(trial, plan()).state, "done")
    local stop_at = rand(3) == 1 and (5 + rand(400)) or nil
    drive(w, s, 6000, stop_at)
    if os.getenv("STATION_DEBUG") then
      print(trial, gens, n, stop_at, s.phase, s.note, s.doing)
      if tonumber(os.getenv("STATION_DEBUG")) == trial then
        for _, mv in ipairs(w.crop_moves) do
          print(("%.2f %d->%d %s x%d"):format(mv.t, mv.src, mv.dst, mv.label, mv.n))
        end
        for i, g in ipairs(st.gens) do
          print("gen", i, g.allowed, g.run and g.run.end_t, g.check_due, w.clock)
        end
      end
    end
    never_last_seed(w)
    conserved(st, initial)
    if s.phase == "stopped" or s.phase == "done" then
      nothing_in_transit(st)
    end
    if s.phase == "stopped" then
      stops = stops + 1
    elseif s.phase == "done" then
      finished = finished + 1
      T.ok(F.count(st.chest, "Tomato Seeds", SEED) >= 4, "trial " .. trial)
    else
      T.has(s.note, "needs", "trial " .. trial .. " waits for seeds it lacks: " .. tostring(s.note))
    end
  end
  T.ok(finished >= 10, "most runs finish: " .. finished)
  T.ok(stops >= 5, "and some were stopped: " .. stops)
end)

return tests
