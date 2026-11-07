-- Component discovery: GT machines, LSC and battery buffers, AE2, chat box, tps card (WO-7.2).
local T = require("testlib")
local F = require("fakeoc")

local tests = {}
local function test(name, fn)
  tests[#tests + 1] = { name, fn }
end

test("the standard base is sorted into roles", function()
  local w = T.base()
  local M, st = T.agent(w)
  local inv = M.discover(st)
  T.eq(#inv.power, 2, "power entries")
  T.eq(inv.power[1].addr, w.buffer.addr)
  T.eq(inv.power[1].kind, "battery_buffer")
  T.eq(inv.power[1].type, "gt_batterybuffer")
  T.eq(inv.power[2].addr, w.lsc.addr)
  T.eq(inv.power[2].kind, "storage", "an LSC is a gt_machine named multimachine.supercapacitor")
  T.eq(inv.power[2].name, "multimachine.supercapacitor")
  T.eq(#inv.machines, 1)
  T.eq(inv.machines[1].addr, w.ebf.addr)
  T.eq(inv.machines[1].name, "multimachine.blastfurnace")
  T.same(inv.machines[1].xyz, { 120, 64, -35 }, "getCoordinates")
  T.eq(inv.me.addr, w.me.addr)
  T.eq(inv.me.type, "me_controller")
  T.eq(inv.chat, w.chat.addr)
  T.eq(inv.tps, w.tps.addr)
  T.eq(inv.counts.gt_machine, 2)
  T.eq(inv.counts.gt_batterybuffer, 1)
  T.eq(inv.counts.internet, 1)
  T.eq(inv.counts.screen, 1)
  T.eq(w.nondirect, 0, "discovery uses only direct calls")
end)

test("an LSC named lsc (no Computronics) is storage too", function()
  local w = T.base({ bare = true })
  F.lsc(w, { type = "lsc", prefix = "4d" })
  local M, st = T.agent(w)
  local inv = M.discover(st)
  T.eq(#inv.power, 1)
  T.eq(inv.power[1].kind, "storage")
  T.eq(inv.power[1].name, "multimachine.supercapacitor")
end)

test("AE2: controller first, a cable interface never", function()
  local w = T.base({ bare = true })
  F.me_interface_part(w, { prefix = "50" })
  local M, st = T.agent(w)
  T.eq(M.discover(st).me, nil, "an interface part has no network methods")
  local block = F.me(w, { prefix = "51", type = "me_interface" })
  T.eq(M.discover(st).me.addr, block.addr, "an interface block works")
  local ctrl = F.me(w, { prefix = "59" })
  T.eq(M.discover(st).me.addr, ctrl.addr, "a controller is preferred")
end)

test("me pins one AE2 component", function()
  local w = T.base({ bare = true, extra = 'me = "510",' })
  local block = F.me(w, { prefix = "51", type = "me_interface" })
  F.me(w, { prefix = "40" })
  local M, st = T.agent(w)
  T.eq(M.discover(st).me.addr, block.addr)
end)

test("ignore and the caps", function()
  local w = T.base({ bare = true, extra = 'ignore = { "2a0" }, max_machines = 2,' })
  F.gt_machine(w, { prefix = "2a", name = "multimachine.vacuumfreezer" })
  for i = 1, 3 do
    F.gt_machine(w, { prefix = "2b", name = "multimachine.multifurnace" .. i })
  end
  local M, st = T.agent(w)
  local inv = M.discover(st)
  T.eq(#inv.machines, 2, "max_machines")
  T.eq(inv.skipped, 1)
  T.eq(inv.counts.gt_machine, 4, "ignored and skipped ones are still counted")
  for _, e in ipairs(inv.machines) do
    T.no(e.addr:sub(1, 2) == "2a", "an ignored prefix")
  end
  T.has(st.errors[1].msg, "left out", "the cap is reported")
end)

test("a machine whose getName fails is still a machine", function()
  local w = T.base({ bare = true })
  local m = F.gt_machine(w, { prefix = "2c", raise = { getName = "not a GregTech machine" } })
  local M, st = T.agent(w)
  local inv = M.discover(st)
  T.eq(#inv.machines, 1)
  T.eq(inv.machines[1].addr, m.addr)
  T.eq(inv.machines[1].name, nil)
end)

test("component signals trigger a rediscovery", function()
  local w = T.base()
  local M, st = T.agent(w)
  local late = F.gt_machine(w, { prefix = "2d", name = "multimachine.implosioncompressor", quiet = true })
  w.devices[late.addr] = nil
  w:at(12, function(world)
    world:add(late) -- pushes component_added
  end)
  w:at(23, function(world)
    world:remove(world.ebf.addr)
  end)
  T.run_until(M, st, w, 31)
  local bodies = T.bodies(w)
  local function has_machine(body, addr)
    for _, m in ipairs(body.machines) do
      if m.addr == addr then
        return true
      end
    end
    return false
  end
  T.no(has_machine(bodies[1], late.addr), "not there at first")
  T.ok(has_machine(bodies[#bodies], late.addr), "found after component_added")
  T.ok(has_machine(bodies[1], w.ebf.addr))
  T.no(has_machine(bodies[#bodies], w.ebf.addr), "dropped after component_removed")
end)

test("a block that vanishes mid-cycle is dropped, not fatal", function()
  local w = T.base()
  local M, st = T.agent(w)
  M.discover(st)
  w.devices[w.ebf.addr] = nil -- broken without a signal reaching the agent yet
  T.ok(M.cycle(st), "the cycle still reaches the core")
  local body = T.last_body(w)
  T.eq(#body.machines, 0)
  local found
  for _, e in ipairs(body.errors) do
    if e.where == "discover" and e.msg:find("is gone", 1, true) then
      found = e
    end
  end
  T.ok(found, "the loss is reported")
  T.ok(st.rediscover, "and the next cycle looks again")
end)

test("rediscovery keeps what the agent knew about an entry", function()
  local w = T.base()
  local M, st = T.agent(w)
  M.discover(st)
  local e = st.inv.by_addr[w.ebf.addr]
  e.sensor = { "x" }
  M.discover(st)
  T.eq(st.inv.by_addr[w.ebf.addr], e)
  T.eq(st.inv.by_addr[w.ebf.addr].sensor[1], "x")
end)

test("--list prints the components and contacts no one", function()
  local w = T.base()
  local M = assert(F.load_agent(w))
  local lines = {}
  local code = M.main(table.pack("--list", "/home/gos/gos-agent.cfg"), { out = function(s)
    lines[#lines + 1] = s
  end })
  T.eq(code, 0)
  local text = table.concat(lines, "\n")
  T.has(text, "2 power, 1 machines, AE2")
  T.has(text, "machine  gt_machine")
  T.has(text, "multimachine.blastfurnace at 120,64,-35")
  T.has(text, "power    gt_batterybuffer")
  T.has(text, "(storage)")
  T.has(text, "gt_machine x2")
  T.eq(#w.requests, 0, "no HTTP request")
  T.eq(w.files["/home/gos/gos-agent.log"], nil, "no log file")
end)

return tests
