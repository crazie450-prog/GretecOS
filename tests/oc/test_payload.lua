-- The telemetry document (gos-oc-bridge/1): shape, raw sensor lines, the watchlist totals, and
-- fresh data kept until the core acks it (WO-7.2). The Python test validates every message the
-- agent sends here against docs/contracts/oc_bridge.schema.json, and compares the golden-*.json
-- dumps with tests/fixtures/oc/.
local T = require("testlib")
local F = require("fakeoc")
local json = require("json")

local tests = {}
local function test(name, fn)
  tests[#tests + 1] = { name, fn }
end

local function by_addr(list, addr)
  for _, e in ipairs(list) do
    if e.addr == addr then
      return e
    end
  end
end

test("the first message", function()
  local w = T.base()
  local M, st = T.agent(w)
  T.ok(M.cycle(st))
  local p = T.last_body(w)
  T.eq(p.v, 1, "contract version")
  T.eq(p.kind, "telemetry")
  T.eq(p.agent, M.VERSION)
  T.eq(p.boot, "b0075eed")
  T.eq(p.seq, 1)
  T.eq(p.transport, "http")
  T.eq(p.cycle_s, 5)
  T.ok(p.uptime >= 1)
  T.ok(p.sample_s > 0, "sampling took ticks")
  T.eq(p.computer.address, w.address)
  T.eq(p.computer.arch, "Lua 5.3")
  T.eq(p.computer.mem_free, 900000)
  T.eq(p.computer.mem_total, 2097152)
  T.eq(p.computer.energy, 4800)
  T.eq(p.tick_ms, 12.35, "the tps card's tick time, rounded")
  T.eq(p.components.gt_machine, 2)
  T.eq(p.components.gt_batterybuffer, 1)
  T.eq(p.components.me_controller, 1)
  -- power
  local buf = by_addr(p.power, w.buffer.addr)
  T.eq(buf.kind, "battery_buffer")
  T.eq(buf.type, "gt_batterybuffer")
  T.eq(buf.name, "batterybuffer.16.tier.04")
  T.same(buf.xyz, { 118, 64, -30 })
  T.eq(buf.eu, 8192)
  T.eq(buf.eu_max, 32768)
  T.eq(buf.eu_in, 0)
  local lsc = by_addr(p.power, w.lsc.addr)
  T.eq(lsc.kind, "storage")
  T.eq(lsc.eu_str, "123456789012345678901234", "exact, beyond 64 bits")
  T.eq(lsc.eu_max_str, "999999999999999999999999")
  T.eq(lsc.eu, nil, "storage sends strings, not numbers")
  -- machines
  local ebf = by_addr(p.machines, w.ebf.addr)
  T.eq(ebf.name, "multimachine.blastfurnace")
  T.eq(ebf.active, true)
  T.eq(ebf.allowed, true)
  T.eq(ebf.work, true)
  T.eq(ebf.progress, 120)
  T.eq(ebf.progress_max, 400)
  -- AE2
  T.eq(p.ae2.addr, w.me.addr)
  T.eq(p.ae2.type, "me_controller")
  T.same(p.ae2.cpus, { { name = "", storage = 65536, coprocessors = 1, busy = false } })
  T.ok(p.ae2.items_at)
  T.eq(#p.crafts, 0)
  T.eq(#p.results, 0)
  T.eq(#p.errors, 0)
end)

test("sensor lines go out raw, byte for byte", function()
  local w = T.base()
  local M, st = T.agent(w)
  M.cycle(st)
  local p = T.last_body(w)
  -- Oldest first, at most 2 a cycle, ties by address: the EBF and the battery buffer now.
  local ebf = by_addr(p.machines, w.ebf.addr)
  T.same(ebf.sensor, w.ebf.sensor, "the EBF's lines unchanged (section signs, escapes)")
  T.eq(ebf.sensor[5], "Problems: \194\167c0", "the Problems line is not parsed here")
  T.eq(ebf.sensor[6], "GT5U.multiblock.scanner.efficiency\\\\\194\167e100\194\167r", "an encoded line keeps its two backslashes")
  local buf = by_addr(p.power, w.buffer.addr)
  T.same(buf.sensor, w.buffer.sensor)
  T.eq(by_addr(p.power, w.lsc.addr).sensor, nil, "the LSC waits for the next cycle")
  T.ok(not w.requests[1].body:find("[\128-\255]"), "the body is pure ASCII")
  M.cycle(st)
  p = T.last_body(w)
  T.eq(#by_addr(p.power, w.lsc.addr).sensor, 24, "all 24 LSC lines")
  T.eq(by_addr(p.machines, w.ebf.addr).sensor, nil, "acked lines are not sent again")
end)

test("the watchlist: totals per registry name and damage", function()
  local w = T.base()
  local M, st = T.agent(w)
  M.cycle(st)
  local items = T.last_body(w).ae2.items
  T.same(items, {
    { name = "minecraft:iron_ingot", damage = 0, size = 1234, stacks = 1, craftable = true },
    { name = "gregtech:gt.metaitem.01", damage = 11305, size = 643, stacks = 2, craftable = true },
    { name = "gregtech:gt.metaitem.01", damage = 2305, size = 0, stacks = 0, craftable = false },
    { name = "minecraft:diamond", damage = 0, size = 0, stacks = 0, craftable = false },
  }, "a plain and an NBT stack of steel ingots add up; unwatched damages are left out")
  T.eq(w.me.last_by_id, 0, "the last call (diamond) found nothing")
  M.cycle(st)
  T.eq(T.last_body(w).ae2.items, nil, "not sent again until the next read")
end)

test("fresh data waits for an ack", function()
  local w = T.base()
  local acks = { 0, 0, 3 } -- ack nothing, nothing, then up to message 3
  local n = 0
  w.server = function(req, world)
    n = n + 1
    return { body = json.encode({ v = 1, ok = true, cmds = json.array(), ack = acks[n] or n }) }
  end
  local M, st = T.agent(w)
  M.cycle(st)
  M.cycle(st)
  M.cycle(st)
  M.cycle(st)
  local b = T.bodies(w)
  T.ok(by_addr(b[1].machines, w.ebf.addr).sensor, "message 1 carries the EBF's lines")
  T.ok(by_addr(b[2].machines, w.ebf.addr).sensor, "message 2 repeats them: not acked")
  T.ok(b[2].ae2.items, "and the watchlist")
  T.ok(by_addr(b[3].machines, w.ebf.addr).sensor, "message 3 too")
  T.eq(by_addr(b[4].machines, w.ebf.addr).sensor, nil, "acked with message 3")
  T.eq(b[4].ae2.items, nil)
end)

test("ok false keeps everything for the next try", function()
  local w = T.base()
  local n = 0
  w.server = function()
    n = n + 1
    if n == 1 then
      return { body = '{"v":1,"ok":false,"cmds":[],"err":"bad_payload"}' }
    end
    return { body = '{"v":1,"ok":true,"cmds":[]}' }
  end
  local M, st = T.agent(w)
  M.cycle(st)
  st.next_cycle = 0
  M.cycle(st)
  local b = T.bodies(w)
  T.ok(by_addr(b[2].machines, w.ebf.addr).sensor, "resent after the refusal")
  local err = b[2].errors[1]
  T.eq(err.where, "transport")
  T.has(err.msg, "bad_payload")
  M.cycle(st)
  T.eq(#T.last_body(w).errors, 0, "the error was acked by message 2's reply")
end)

test("values a driver can't give are left out, not invented", function()
  local w = T.base({ bare = true })
  local m = F.gt_machine(w, { prefix = "2e0", without = { "getEUStored", "getEUMaxStored" } })
  m.methods.getWorkProgress = { direct = true, fn = function() return "busy" end }
  local M, st = T.agent(w)
  M.cycle(st)
  M.cycle(st)
  local e = by_addr(T.last_body(w).machines, m.addr)
  T.eq(e.eu, nil)
  T.eq(e.progress, nil, "a non-number progress is dropped")
  T.eq(e.active, true)
  local errs = T.bodies(w)[1].errors
  T.eq(#errs, 2, "one record per missing method")
  T.has(errs[1].msg, "no such method")
  T.eq(#T.bodies(w)[2].errors, 0, "a method the block lacks is not tried again")
end)

test("repeated errors are counted, and bounded", function()
  local w = T.base()
  w.server = function()
    return { fail = "Connection refused (Connection refused)" }
  end
  local M, st = T.agent(w)
  for _ = 1, 30 do
    st.next_cycle = 0
    M.cycle(st)
  end
  T.eq(#st.errors, 1, "one record")
  T.eq(st.errors[1].count, 30)
  for i = 1, 20 do
    M.record_error(st, "machines", "error " .. i)
  end
  T.eq(#st.errors, 8, "at most 8 records")
end)

-- The golden scenario: the messages and reply tests/fixtures/oc/ keeps as examples for WO-7.1.
test("golden", function()
  local w = T.base()
  local n = 0
  w.server = function(req, world)
    n = n + 1
    local reply = { v = 1, ok = true, cmds = json.array() }
    if n == 2 then
      reply.cmds = json.array({
        T.cmd(101, "say", { text = "Craft started" }, "dashboard"),
        T.cmd(102, "craft", { name = "gregtech:gt.metaitem.01", damage = 11305, amount = 64 }, "voice"),
        T.cmd(103, "machine_pause", { addr = world.ebf.addr }, "phone"),
      })
      T.dump("golden-reply-commands.json", json.encode(reply))
    end
    return { body = json.encode(reply) }
  end
  local M, st = T.agent(w)
  M.cycle(st) -- 1: full first read
  M.cycle(st) -- 2: the commands arrive
  M.cycle(st) -- 3: results and the craft (planning done)
  T.dump("golden-telemetry-first.json", w.requests[1].body)
  T.dump("golden-telemetry-results.json", w.requests[3].body)
  T.dump("golden-reply-empty.json", '{"v":1,"ok":true,"cmds":[]}')
  T.dump("golden-reply-refused.json", '{"v":1,"ok":false,"cmds":[],"err":"unauthorized"}')
  local p3 = T.last_body(w)
  T.eq(#p3.results, 3)
  T.eq(p3.crafts[1].state, "crafting")
end)

return tests
