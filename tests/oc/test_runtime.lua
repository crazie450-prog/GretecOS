-- The run loop: it yields, stops cleanly on Ctrl+C and Ctrl+Alt+C, survives errors, keeps memory
-- flat over hours, bounds its log, and refuses a Lua 5.2 CPU with a clear message (WO-7.2).
local T = require("testlib")
local F = require("fakeoc")
local json = require("json")

local tests = {}
local function test(name, fn)
  tests[#tests + 1] = { name, fn }
end

local function listeners_left(w)
  local n = 0
  for name, list in pairs(w.listeners) do
    if name == "interrupted" or name:find("^component_") then
      n = n + #list
    end
  end
  return n
end

test("Ctrl+C stops it at the next wait, listeners removed", function()
  local w = T.base()
  local M, st = T.agent(w)
  w:signal_at(33, "interrupted", 33)
  local ok, err = M.run(st)
  T.ok(ok, err)
  T.between(w.clock, 33, 33.5, "stopped when the signal came")
  T.eq(listeners_left(w), 0, "OpenOS listeners outlive a program unless removed")
  T.has(w.files["/home/gos/gos-agent.log"], "stopped")
end)

test("Ctrl+Alt+C while waiting for the core", function()
  local w = T.base()
  w.server = function()
    return { stall_connect = true }
  end
  local M, st = T.agent(w)
  local ok, err = T.run_until(M, st, w, 6) -- inside the first exchange's wait
  T.ok(ok, err)
  T.eq(w.card.nopen(), 0, "the connection is closed on the way out")
  T.eq(listeners_left(w), 0)
end)

test("Ctrl+Alt+C during a pause command's wait", function()
  local w = T.base()
  local M, st = T.agent(w)
  w.cmds_queue = { T.cmd(1, "machine_pause", { addr = w.ebf.addr }) }
  local t_reply
  local inner = w.server
  w.server = function(req, world)
    t_reply = world.clock
    return inner(req, world)
  end
  M.cycle(st)
  -- The pause ran inside that cycle and slept 0.1 s; now interrupt in the next one's pause.
  w.cmds_queue = { T.cmd(2, "machine_resume", { addr = w.ebf.addr }) }
  st.next_cycle = 0
  w.hard_interrupt_at = w.clock + 0.4
  local ok = M.run(st)
  T.ok(ok, "a clean stop, not a crash")
  T.ok(t_reply)
end)

test("it always yields between cycles", function()
  local w = T.base()
  local pulls = 0
  local pull = w.event.pull
  w.event.pull = function(...)
    pulls = pulls + 1
    return pull(...)
  end
  local M, st = T.agent(w)
  T.run_until(M, st, w, 301)
  T.ok(pulls >= st.cycles, ("waited in event.pull %d times over %d cycles"):format(pulls, st.cycles))
end)

test("errors inside a cycle never stop it", function()
  local w = T.base()
  w.ebf.raise.isMachineActive = "java.lang.NullPointerException"
  w.lsc.raise.getStoredEUString = "not enough memory"
  w.me.raise.getCpus = { "boom", "boom" }
  local M, st = T.agent(w)
  T.run_until(M, st, w, 61)
  T.ok(w.nrequests >= 11, "kept sending")
  local wheres = {}
  for _, b in ipairs(T.bodies(w)) do
    for _, e in ipairs(b.errors) do
      wheres[e.where .. ":" .. e.msg:match("[^:]+$")] = true
    end
  end
  T.ok(wheres["machines: java.lang.NullPointerException"], "machine error reported")
  T.ok(wheres["power: not enough memory"], "an out-of-memory in a call is reported, not fatal")
  T.ok(wheres["ae2: boom"])
end)

test("two hours: flat memory, bounded state", function()
  -- No log file here: the fake drive keeps files in Lua memory, which would be counted (the
  -- log's own bound is the next test's).
  local w = T.base({ extra = "log = false," })
  w.lean = true
  local n = 0
  w.server = function(req, world)
    n = n + 1
    local reply = { v = 1, ok = true, cmds = json.array() }
    if n % 7 == 0 then
      reply.cmds = json.array({ T.cmd(n, "say", { text = "tick " .. n }) })
    elseif n % 11 == 0 then
      reply.cmds = json.array({ T.cmd(n, "craft", { name = "gregtech:gt.metaitem.01", damage = 11305, amount = 1 }) })
    elseif n % 13 == 0 then
      return { fail = "Connection refused" }
    end
    return { body = json.encode(reply) }
  end
  w.me.patterns["gregtech:gt.metaitem.01@11305"].craft_ticks = 200
  local M, st = T.agent(w)
  local function heap_kb()
    w.chat.said, w.me.requests = {}, {} -- the fake's own records, not the agent's
    collectgarbage("collect")
    collectgarbage("collect")
    return collectgarbage("count")
  end
  T.run_until(M, st, w, 601)
  local kb1 = heap_kb()
  T.run_until(M, st, w, 7201)
  local kb2 = heap_kb()
  T.ok(#w.timers < 20, "the fake's timers don't pile up: " .. #w.timers)
  T.ok(st.cycles > 1300, "cycles: " .. st.cycles)
  T.ok(kb2 - kb1 < 64, ("Lua heap grew %.1f KB between 10 min and 2 h"):format(kb2 - kb1))
  T.ok(#st.errors <= 8)
  T.ok(#st.results <= 16)
  T.ok(#st.crafts <= 8)
  T.ok(#st.done_order <= 32)
  local nlogged = 0
  for _ in pairs(st.logged) do
    nlogged = nlogged + 1
  end
  T.ok(nlogged <= 32)
  local log = w.files["/home/gos/gos-agent.log"] or ""
  T.ok(#log <= 32768, "the log is rotated: " .. #log .. " bytes")
  T.ok(#(w.files["/home/gos/gos-agent.state"] or "") < 4096, "the state file stays small")
  T.dump("memory-stats.json", json.encode({
    cycles = st.cycles,
    heap_kb_at_10min = math.floor(kb1 + 0.5),
    heap_kb_at_2h = math.floor(kb2 + 0.5),
    growth_kb = math.floor((kb2 - kb1) * 10 + 0.5) / 10,
  }))
end)

test("the log rotates and carries a heartbeat", function()
  local w = T.base({ extra = "log_max = 4096," })
  local M, st = T.agent(w)
  for i = 1, 200 do
    M.record_error(st, "machines", "distinct error " .. i)
  end
  T.ok(#w.files["/home/gos/gos-agent.log"] <= 4096)
  T.ok(w.files["/home/gos/gos-agent.log.1"], "the previous log is kept as .1")
  local w2 = T.base()
  local M2, st2 = T.agent(w2)
  T.run_until(M2, st2, w2, 610)
  local beats = 0
  for _ in w2.files["/home/gos/gos-agent.log"]:gmatch("alive: ") do
    beats = beats + 1
  end
  T.eq(beats, 3, "at start, 5 and 10 minutes")
end)

test("a Lua 5.2 CPU gets a clear message", function()
  local w = T.base()
  local mod, msg = F.load_agent(w, { version = "Lua 5.2" })
  T.eq(mod, nil)
  T.has(msg, "needs a Lua 5.3 CPU")
  T.has(msg, "shift-right-click")
  package.loaded.component = w.component
  local env = setmetatable({ print = function(...) w:print(...) end, _VERSION = "Lua 5.2" }, { __index = _G })
  local chunk = assert(loadfile(F.ROOT .. "/oc/gos-agent.lua", "t", env))
  T.eq(chunk(), 1, "as a program it prints and exits 1")
  T.has(w:printed_text(), "Lua 5.3")
end)

local function once(w)
  local M = assert(F.load_agent(w))
  local lines = {}
  local code = M.main(table.pack("--once", "/home/gos/gos-agent.cfg"), {
    out = function(s)
      lines[#lines + 1] = s
    end,
    boot = "b0075eed",
  })
  return code, table.concat(lines, "\n")
end

test("--once: one cycle and an answer", function()
  local w = T.base()
  local code, text = once(w)
  T.eq(code, 0)
  T.has(text, "the core answered")
  T.eq(w.nrequests, 1)
  local w2 = T.base()
  w2.server = function()
    return { fail = "Connection refused" }
  end
  code, text = once(w2)
  T.eq(code, 1)
  T.has(text, "failed: Connection refused")
  local w3 = T.base({ config = "{}" })
  code, text = once(w3)
  T.eq(code, 1)
  T.has(text, "token")
end)

test("--once with commands: it reports their results before it exits", function()
  local w = T.base()
  w.cmds_queue = {
    T.cmd(1, "say", { text = "hello from --once" }),
    T.cmd(2, "craft", { name = "gregtech:gt.metaitem.01", damage = 11305, amount = 1 }),
  }
  local code, text = once(w)
  T.eq(code, 0)
  T.has(text, "the core answered")
  T.has(text, "their results went in the later ones")
  local state = {}
  for _, b in ipairs(T.bodies(w)) do
    for _, r in ipairs(b.results) do
      state[r.id] = r.state
    end
  end
  T.eq(state[1], "done")
  T.eq(state[2], "submitted", "AE2's answer, not 'restarted before AE2 answered' on the next run")
  T.between(w.nrequests, 2, 4)
  T.ok(w.clock < 10, "it doesn't linger")
  -- AE2 never finishes planning: it stops waiting after 30 s and says what that means.
  local w2 = T.base()
  w2.me.patterns["minecraft:diamond@0"] = { plan_forever = true }
  w2.cmds_queue = { T.cmd(3, "craft", { name = "minecraft:diamond", damage = 0, amount = 1 }) }
  code, text = once(w2)
  T.eq(code, 0)
  T.has(text, "a result was still waiting after 30 s")
  T.between(w2.clock, 30, 36)
end)

test("an unknown option is a usage error", function()
  local w = T.base()
  local M = assert(F.load_agent(w))
  local out = {}
  T.eq(M.main(table.pack("--fast"), { out = function(s) out[#out + 1] = s end }), 2)
  T.has(out[1], "usage: gos-agent")
end)

return tests
