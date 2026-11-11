-- Commands from the core (plan 7.9 safe actions): only craft, machine_pause, machine_resume and
-- say run, only when the command says how Clayton confirmed it, only with valid arguments, and
-- never twice; every one gets a result (WO-7.2).
local T = require("testlib")
local F = require("fakeoc")
local json = require("json")

local tests = {}
local function test(name, fn)
  tests[#tests + 1] = { name, fn }
end

-- Send cmds in the next reply, run cycles, return the results seen in later messages by id.
local function exchange(w, M, st, cmds, cycles)
  w.cmds_queue = cmds
  for _ = 1, cycles or 3 do
    st.next_cycle = 0
    M.cycle(st)
  end
  local results = {}
  for _, b in ipairs(T.bodies(w)) do
    for _, r in ipairs(b.results) do
      results[r.id] = r
    end
  end
  return results
end

local function result_of(w, id)
  local last
  for _, b in ipairs(T.bodies(w)) do
    for _, r in ipairs(b.results) do
      if r.id == id then
        last = r
      end
    end
  end
  return last
end

test("say", function()
  local w = T.base()
  w.bad_cmds = true -- some of these break the schema on purpose
  local M, st = T.agent(w)
  local r = exchange(w, M, st, {
    T.cmd(1, "say", { text = "Craft 64 stainless steel ingots started." }),
    T.cmd(2, "say", { text = "\194\167kobfuscated\194\167r\nsecond line\0" }),
    T.cmd(3, "say", { text = string.rep("x", 300) }),
    T.cmd(4, "say", { text = "  " }),
    T.cmd(5, "say", { text = 42 }),
  })
  T.eq(r[1].ok, true)
  T.eq(r[1].state, "done")
  T.eq(r[1].kind, "say")
  T.eq(w.chat.said[1], "Craft 64 stainless steel ingots started.")
  T.eq(w.chat.said[2], "obfuscated second line", "colour codes and control characters go")
  T.eq(r[3].state, "refused", "over 256 bytes")
  T.eq(r[4].state, "refused", "nothing left to say")
  T.eq(r[5].state, "refused", "not a string")
  T.eq(#w.chat.said, 2)
end)

test("say without a chat box fails", function()
  local w = T.base()
  w.devices[w.chat.addr] = nil
  local M, st = T.agent(w)
  local r = exchange(w, M, st, { T.cmd(1, "say", { text = "hi" }) })
  T.eq(r[1].state, "failed")
  T.has(r[1].err, "no chat box")
end)

test("a chat box that answers nil and a reason didn't say it", function()
  local w = T.base()
  w.chat.fail.say = "unknown error" -- OC: the method's Java side threw
  local M, st = T.agent(w)
  local r = exchange(w, M, st, { T.cmd(1, "say", { text = "hi" }) })
  T.eq(r[1].ok, false)
  T.eq(r[1].state, "failed")
  T.eq(r[1].err, "unknown error")
  T.eq(#w.chat.said, 0)
end)

test("machine_pause and machine_resume", function()
  local w = T.base()
  local M, st = T.agent(w)
  local r = exchange(w, M, st, { T.cmd(1, "machine_pause", { addr = w.ebf.addr }) })
  T.eq(r[1].state, "done")
  T.eq(r[1].ok, true)
  T.eq(w.ebf.allowed, false)
  local last = T.last_body(w)
  T.eq(last.machines[1].allowed, false, "telemetry shows it paused")
  r = exchange(w, M, st, { T.cmd(2, "machine_resume", { addr = w.ebf.addr }) })
  T.eq(r[2].state, "done")
  T.eq(w.ebf.allowed, true)
  T.eq(w:calls_of("gt_machine.setWorkAllowed"), 2)
end)

test("pause refuses anything but a found machine", function()
  local w = T.base()
  w.bad_cmds = true
  local M, st = T.agent(w)
  local r = exchange(w, M, st, {
    T.cmd(1, "machine_pause", { addr = "deadbeef-0000-4000-8000-000000000000" }),
    T.cmd(2, "machine_pause", { addr = w.buffer.addr }),
    T.cmd(3, "machine_resume", { addr = w.chat.addr }),
    T.cmd(4, "machine_pause", {}),
  })
  for id = 1, 4 do
    T.eq(r[id].state, "refused", "command " .. id)
  end
  T.eq(w:calls_of("gt_machine.setWorkAllowed") + w:calls_of("gt_batterybuffer.setWorkAllowed"), 0)
end)

test("a pause the machine ignores is reported as failed", function()
  local w = T.base()
  w.ebf.ignore_work = true
  local M, st = T.agent(w)
  local r = exchange(w, M, st, { T.cmd(1, "machine_pause", { addr = w.ebf.addr }) })
  T.eq(r[1].state, "failed")
  T.has(r[1].err, "still says work allowed = true")
end)

test("a pause or a craft whose call OC answers with nil and a reason failed, with that reason", function()
  local w = T.base()
  w.ebf.fail.setWorkAllowed = { "unknown error" } -- once
  w.me.fail.getCraftable = { "unknown error" }
  local M, st = T.agent(w)
  local r = exchange(w, M, st, {
    T.cmd(1, "machine_pause", { addr = w.ebf.addr }),
    T.cmd(2, "craft", { name = "gregtech:gt.metaitem.01", damage = 11305, amount = 4 }),
  })
  T.eq(r[1].state, "failed")
  T.eq(r[1].err, "setWorkAllowed: unknown error")
  T.eq(w.ebf.allowed, true)
  T.eq(r[2].state, "failed")
  T.eq(r[2].err, "getCraftable: unknown error", "not 'no pattern': the lookup failed")
  T.eq(w:calls_of("craftable.request"), 0)
  w.ebf.fail.isWorkAllowed = "Index: 3, Size: 2"
  r = exchange(w, M, st, { T.cmd(3, "machine_pause", { addr = w.ebf.addr }) })
  T.eq(r[3].state, "failed")
  T.eq(r[3].err, "isWorkAllowed: Index: 3, Size: 2", "the check failed; it doesn't claim the machine said anything")
end)

test("craft: planned, submitted, crafted", function()
  local w = T.base()
  w.me.patterns["gregtech:gt.metaitem.01@11305"].plan_ticks = 40 -- 2 s of planning
  local M, st = T.agent(w)
  w.cmds_queue = { T.cmd(7, "craft", { name = "gregtech:gt.metaitem.01", damage = 11305, amount = 64 }) }
  M.cycle(st) -- the reply carries the command
  T.eq(#w.me.requests, 1)
  T.eq(w.me.requests[1].amount, 64)
  T.eq(w.me.requests[1].key, "gregtech:gt.metaitem.01@11305")
  T.eq(st.crafts[1].state, "computing")
  T.eq(st.next_cycle - w.clock < 1.1, true, "the result is reported soon")
  T.run_until(M, st, w, w.clock + 20)
  local r = result_of(w, 7)
  T.eq(r.state, "submitted")
  T.eq(r.ok, true)
  local states = {}
  for _, b in ipairs(T.bodies(w)) do
    for _, c in ipairs(b.crafts) do
      if states[#states] ~= c.state then
        states[#states + 1] = c.state
      end
    end
  end
  T.same(states, { "computing", "crafting", "done" })
  T.eq(#T.last_body(w).crafts, 0, "a finished craft is dropped once acked")
  T.eq(#st.crafts, 0)
end)

test("craft: AE2 refuses, cancels, has no pattern", function()
  local w = T.base()
  w.me.patterns["minecraft:diamond@0"] = { plan_ticks = 2, craft_ticks = 10, cancel = true }
  local M, st = T.agent(w)
  exchange(w, M, st, {
    T.cmd(1, "craft", { name = "minecraft:iron_ingot", damage = 0, amount = 10 }),
    T.cmd(2, "craft", { name = "minecraft:diamond", damage = 0, amount = 1 }),
    T.cmd(3, "craft", { name = "minecraft:stone", damage = 0, amount = 1 }),
  }, 1)
  T.run_until(M, st, w, w.clock + 20)
  T.eq(result_of(w, 1).state, "failed")
  T.has(result_of(w, 1).err, "missing resources")
  T.eq(result_of(w, 2).state, "submitted")
  T.eq(result_of(w, 3).state, "failed")
  T.has(result_of(w, 3).err, "no pattern")
  local canceled = false
  for _, b in ipairs(T.bodies(w)) do
    for _, c in ipairs(b.crafts) do
      if c.id == 2 and c.state == "canceled" then
        canceled = true
      end
    end
  end
  T.ok(canceled, "the canceled job is reported")
end)

test("craft arguments are checked", function()
  local w = T.base()
  w.bad_cmds = true
  local M, st = T.agent(w)
  local bad = {
    { name = "gregtech:gt.metaitem.01", damage = 11305, amount = 0 },
    { name = "gregtech:gt.metaitem.01", damage = 11305, amount = -5 },
    { name = "gregtech:gt.metaitem.01", damage = 11305, amount = 1.5 },
    { name = "gregtech:gt.metaitem.01", damage = 11305, amount = 1000001 },
    { name = "gregtech:gt.metaitem.01", damage = 40000, amount = 1 },
    { name = "gregtech:gt.metaitem.01", amount = 1 },
    { name = 265, damage = 0, amount = 1 },
    { name = "steel ingot", damage = 0, amount = 1 },
    { name = "gregtech:gt.metaitem.01", damage = 11305, amount = 1, cpu = 5 },
    { name = "gregtech:" .. ("x"):rep(120), damage = 0, amount = 1 }, -- over 128 bytes
  }
  local cmds = {}
  for i, a in ipairs(bad) do
    cmds[i] = T.cmd(i, "craft", a)
  end
  local r = exchange(w, M, st, { table.unpack(cmds, 1, 8) }) -- at most 8 a reply
  for k, v in pairs(exchange(w, M, st, { table.unpack(cmds, 9) })) do
    r[k] = v
  end
  for i = 1, #bad do
    T.eq(r[i].state, "refused", "bad craft " .. i)
  end
  T.eq(#w.me.requests, 0, "nothing reached AE2")
end)

test("craft without AE2 fails, and max_crafts holds", function()
  local w = T.base({ extra = "max_crafts = 1," })
  w.me.patterns["minecraft:diamond@0"] = { plan_ticks = 2, craft_ticks = 100000 }
  local M, st = T.agent(w)
  local r = exchange(w, M, st, {
    T.cmd(1, "craft", { name = "minecraft:diamond", damage = 0, amount = 1 }),
    T.cmd(2, "craft", { name = "minecraft:diamond", damage = 0, amount = 1 }),
  })
  T.eq(r[1].state, "submitted")
  T.eq(r[2].state, "failed")
  T.has(r[2].err, "max_crafts")
  local w2 = T.base({ bare = true })
  local M2, st2 = T.agent(w2)
  local r2 = exchange(w2, M2, st2, { T.cmd(1, "craft", { name = "minecraft:diamond", damage = 0, amount = 1 }) })
  T.eq(r2[1].state, "failed")
  T.has(r2[1].err, "no AE2")
end)

test("finished crafts the core never acknowledges stay at most 16", function()
  local w = T.base()
  w.me.patterns["minecraft:diamond@0"] = { plan_ticks = 1, craft_ticks = 1 }
  local id = 0
  w.server = function()
    id = id + 1
    local cmds = json.array({ T.cmd(id, "craft", { name = "minecraft:diamond", damage = 0, amount = 1 }) })
    return { body = json.encode({ v = 1, ok = true, cmds = cmds, ack = 0 }) }
  end
  local M, st = T.agent(w)
  for _ = 1, 24 do
    st.next_cycle = 0
    M.cycle(st)
    T.ok(#st.crafts <= 16, "crafts kept: " .. #st.crafts)
  end
  T.eq(#st.crafts, 16)
  local dropped = false
  for _, e in ipairs(st.errors) do
    dropped = dropped or e.msg:find("final state was dropped", 1, true) ~= nil
  end
  T.ok(dropped, "the drop is reported")
  local running = 0
  for _, c in ipairs(st.crafts) do
    if c.state == "computing" or c.state == "crafting" then
      running = running + 1
    end
  end
  T.ok(running >= 1, "a running craft is never the one dropped")
end)

test("a craft AE2 never finishes planning gives up after 300 s", function()
  local w = T.base()
  w.me.patterns["minecraft:diamond@0"] = { plan_forever = true }
  local M, st = T.agent(w)
  exchange(w, M, st, { T.cmd(1, "craft", { name = "minecraft:diamond", damage = 0, amount = 1 }) }, 1)
  T.run_until(M, st, w, w.clock + 320)
  local r = result_of(w, 1)
  T.eq(r.state, "failed")
  T.has(r.err, "still planning")
end)

test("only allowlisted, confirmed commands with ids run", function()
  local w = T.base()
  w.bad_cmds = true
  local M, st = T.agent(w)
  local r = exchange(w, M, st, {
    T.cmd(1, "self_destruct", {}),
    T.cmd(2, "waypoint", { x = 1 }),
    T.cmd(3, "say", { text = "unconfirmed" }, false),
    T.cmd(4, "say", { text = "empty confirmation" }, ""),
    { id = 5, kind = "say", args = json.array({ "x" }), confirmed_via = "voice" },
    { kind = "say", args = { text = "no id" }, confirmed_via = "voice" },
    { id = -1, kind = "say", args = { text = "negative id" }, confirmed_via = "voice" },
    { id = 2.5, kind = "say", args = { text = "fractional id" }, confirmed_via = "voice" },
    "not even a table",
  })
  T.eq(r[1].state, "refused")
  T.has(r[1].err, "not allowed")
  T.eq(r[2].state, "refused", "waypoint is the core's job, not OC's")
  T.eq(r[3].state, "refused")
  T.has(r[3].err, "not confirmed")
  T.eq(r[4].state, "refused")
  T.eq(r[5].state, "refused", "args must be an object")
  T.eq(#w.chat.said, 0, "nothing was said")
  local ignored = 0
  for _, e in ipairs(T.bodies(w)[2].errors) do
    if e.where == "command" then
      ignored = ignored + e.count
    end
  end
  T.eq(ignored, 4, "commands without a usable id are reported as errors, not results")
end)

test("confirmed_via is one of the contract's four ways, nothing else", function()
  local w = T.base()
  w.bad_cmds = true
  local M, st = T.agent(w)
  local r = exchange(w, M, st, {
    T.cmd(1, "say", { text = "by voice" }, "voice"),
    T.cmd(2, "say", { text = "by the dashboard" }, "dashboard"),
    T.cmd(3, "say", { text = "an agent" }, "agent"),
    T.cmd(4, "say", { text = "a blank" }, " "),
    T.cmd(5, "say", { text = "capitalised" }, "Voice"),
    T.cmd(6, "say", { text = "padded" }, "phone "),
    T.cmd(7, "say", { text = "a number" }, 1),
  })
  T.eq(r[1].state, "done")
  T.eq(r[2].state, "done")
  for id = 3, 7 do
    T.eq(r[id].state, "refused", "command " .. id)
    T.has(r[id].err, "not confirmed")
  end
  T.same(w.chat.said, { "by voice", "by the dashboard" })
end)

-- OC 1.12.68's CraftingStatus.save/load: after a reload the status has no AE2 link and answers
-- with the saved flag and "no link" (fakeoc's reload_jobs).
test("a world reload: crafts OC lost track of are said to be lost, never canceled", function()
  local w = T.base()
  w.me.patterns["minecraft:diamond@0"] = { plan_ticks = 400 } -- still planning at the reload
  w.me.patterns["gregtech:gt.metaitem.01@11305"].craft_ticks = 4000 -- crafting at the reload
  w.me.patterns["minecraft:gold_ingot@0"] = { plan_ticks = 2, craft_ticks = 4000 }
  local M, st = T.agent(w)
  exchange(w, M, st, {
    T.cmd(1, "craft", { name = "minecraft:diamond", damage = 0, amount = 1 }),
    T.cmd(2, "craft", { name = "gregtech:gt.metaitem.01", damage = 11305, amount = 1 }),
    T.cmd(3, "craft", { name = "minecraft:gold_ingot", damage = 0, amount = 1 }),
  }, 1)
  T.run_until(M, st, w, w.clock + 6)
  T.eq(st.crafts[1].state, "computing")
  T.eq(st.crafts[2].state, "crafting")
  T.eq(st.crafts[3].state, "crafting")
  w.me.requests[3].done = true -- AE2 finished it after the agent's last look
  w.me:reload_jobs()
  T.run_until(M, st, w, w.clock + 12)
  local r1 = result_of(w, 1)
  T.eq(r1.state, "failed", "planning never comes back after a reload")
  T.has(r1.err, "lost track of it after a world reload")
  T.has(r1.err, "may still run")
  T.eq(result_of(w, 2).state, "submitted", "its result went before the reload")
  local last = {}
  for _, b in ipairs(T.bodies(w)) do
    for _, c in ipairs(b.crafts) do
      T.ok(c.state ~= "canceled", "craft " .. c.id .. " is never reported canceled")
      last[c.id] = c
    end
  end
  T.eq(last[2].state, "failed")
  T.has(last[2].err, "may still run, check AE2")
  T.eq(last[3].state, "done", "a job done before the save reads done")
end)

-- CraftingStatus.save also runs at an autosave, with no reload, and sets the live status's
-- failed flag while AE2 plans; AE2 then links the job, and isDone answers from the link.
test("an autosave while AE2 plans doesn't turn a craft into a failure", function()
  local w = T.base()
  w.me.patterns["minecraft:diamond@0"] = { plan_ticks = 100, craft_ticks = 40 }
  w.me.patterns["minecraft:gold_ingot@0"] = { plan_ticks = 100, fail = "missing resources" }
  local M, st = T.agent(w)
  exchange(w, M, st, {
    T.cmd(1, "craft", { name = "minecraft:diamond", damage = 0, amount = 1 }),
    T.cmd(2, "craft", { name = "minecraft:gold_ingot", damage = 0, amount = 1 }),
  }, 1)
  T.eq(st.crafts[1].state, "computing")
  w.me:autosave_jobs()
  T.run_until(M, st, w, w.clock + 20)
  T.eq(result_of(w, 1).state, "submitted", "AE2 took the job after the save")
  local states = {}
  for _, b in ipairs(T.bodies(w)) do
    for _, c in ipairs(b.crafts) do
      if c.id == 1 and states[#states] ~= c.state then
        states[#states + 1] = c.state
      end
    end
  end
  T.same(states, { "computing", "crafting", "done" })
  T.eq(result_of(w, 2).state, "failed")
  T.has(result_of(w, 2).err, "request failed (missing resources)", "AE2's own reason, not a lost link")
end)

test("a command is never run twice", function()
  local w = T.base()
  local M, st = T.agent(w)
  local say = T.cmd(9, "say", { text = "once" })
  exchange(w, M, st, { say, say })
  exchange(w, M, st, { say })
  T.eq(#w.chat.said, 1)
  local reported = 0
  for _, b in ipairs(T.bodies(w)) do
    for _, r in ipairs(b.results) do
      if r.id == 9 then
        reported = reported + 1
        T.eq(r.state, "done")
      end
    end
  end
  T.eq(reported, 2, "re-delivery is answered with the stored result")
end)

test("the executed-id memory survives a restart", function()
  local w = T.base()
  w.me.patterns["minecraft:diamond@0"] = { plan_forever = true }
  local M, st = T.agent(w)
  exchange(w, M, st, {
    T.cmd(20, "say", { text = "before the restart" }),
    T.cmd(21, "craft", { name = "minecraft:diamond", damage = 0, amount = 1 }),
  }, 1)
  T.ok(w.files["/home/gos/gos-agent.state"], "the state file is written")
  -- the agent restarts while AE2 is still planning command 21
  local M2, st2 = T.agent(w, { boot = "b0075eef" })
  local r = exchange(w, M2, st2, {
    T.cmd(20, "say", { text = "before the restart" }),
    T.cmd(21, "craft", { name = "minecraft:diamond", damage = 0, amount = 1 }),
  })
  T.eq(#w.chat.said, 1, "not said again")
  T.eq(r[20].state, "done")
  T.eq(r[21].state, "failed")
  T.has(r[21].err, "restarted before AE2 answered")
end)

test("a broken state file is ignored, not fatal", function()
  local w = T.base()
  w.files["/home/gos/gos-agent.state"] = "{not json"
  local M, st = T.agent(w)
  T.has(st.errors[1].msg, "unreadable")
  local r = exchange(w, M, st, { T.cmd(1, "say", { text = "hi" }) })
  T.eq(r[1].state, "done")
end)

test("at most 8 commands per reply, none from a refusal", function()
  local w = T.base()
  local cmds = {}
  for i = 1, 10 do
    cmds[i] = T.cmd(i, "say", { text = "line " .. i })
  end
  w.bad_cmds = true -- 10 is over the contract's 8
  local M, st = T.agent(w)
  exchange(w, M, st, cmds)
  T.eq(#w.chat.said, 8)
  local w2 = T.base()
  w2.server = function()
    return { body = json.encode({ v = 1, ok = false, err = "unauthorized", cmds = json.array({ T.cmd(1, "say", { text = "no" }) }) }) }
  end
  local M2, st2 = T.agent(w2)
  M2.cycle(st2)
  T.eq(#w2.chat.said, 0, "commands in a refused reply are ignored")
end)

test("property: nothing outside the rules ever runs", function()
  math.randomseed(7202)
  local w = T.base()
  w.bad_cmds = true
  local M, st = T.agent(w)
  local kinds = { "craft", "machine_pause", "machine_resume", "say", "nuke", "waypoint", "", 5 }
  local vias = { "voice", "dashboard", "phone", "discord", false, "", 7, "agent", " ", "Voice", "discord " }
  local confirmed_by = { voice = true, dashboard = true, phone = true, discord = true }
  local expected_say, expected_pause, expected_craft = 0, 0, 0
  local next_id = 1
  for _ = 1, 60 do
    local cmds = {}
    for _ = 1, math.random(1, 8) do
      local kind = kinds[math.random(#kinds)]
      local via = vias[math.random(#vias)]
      local args
      if math.random() < 0.15 then
        args = json.array({ 1 })
      elseif kind == "say" then
        args = { text = math.random() < 0.8 and ("msg " .. next_id) or "" }
      elseif kind == "craft" then
        args = { name = "gregtech:gt.metaitem.01", damage = 11305, amount = math.random(-2, 3) }
      elseif kind == "machine_pause" or kind == "machine_resume" then
        args = { addr = math.random() < 0.7 and w.ebf.addr or w.buffer.addr }
      else
        args = { x = 1 }
      end
      local id = math.random() < 0.1 and math.max(1, next_id - 3) or next_id
      local confirmed = confirmed_by[via] == true
      local fresh = not st.done[id]
      local valid_args = not json.is_array(args)
      if fresh and confirmed and valid_args and id == next_id then
        if kind == "say" and args.text ~= "" then
          expected_say = expected_say + 1
        elseif (kind == "machine_pause" or kind == "machine_resume") and args.addr == w.ebf.addr then
          expected_pause = expected_pause + 1
        elseif kind == "craft" and args.amount >= 1 then
          expected_craft = expected_craft + 1
        end
      end
      if id == next_id then
        next_id = next_id + 1
      end
      local cmd = { id = id, kind = kind, args = valid_args and json.object(args) or args }
      if via ~= false then
        cmd.confirmed_via = via
      end
      -- one id per reply at most, so a duplicate within a reply doesn't skew the count
      local dup = false
      for _, c in ipairs(cmds) do
        if c.id == id then
          dup = true
        end
      end
      if not dup then
        cmds[#cmds + 1] = cmd
      end
    end
    w.cmds_queue = cmds
    st.next_cycle = 0
    M.cycle(st)
    T.run_until(M, st, w, w.clock + 6)
  end
  T.eq(#w.chat.said, expected_say, "say ran exactly for the valid ones")
  T.eq(w:calls_of("gt_machine.setWorkAllowed"), expected_pause, "setWorkAllowed only on the confirmed machine")
  T.eq(w:calls_of("gt_batterybuffer.setWorkAllowed"), 0, "never on a battery buffer")
  T.eq(#w.me.requests, expected_craft, "AE2 requests only for valid crafts")
  -- every id that got a result got exactly one distinct outcome
  local outcome = {}
  for _, b in ipairs(T.bodies(w)) do
    for _, r in ipairs(b.results) do
      if outcome[r.id] and r.state ~= outcome[r.id] then
        T.fail("command " .. r.id .. " reported as " .. outcome[r.id] .. " and " .. r.state)
      end
      outcome[r.id] = r.state
    end
  end
end)

return tests
