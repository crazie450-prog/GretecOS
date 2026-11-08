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
      return { body = '{"v":1,"ok":false,"cmds":[],"err":"internal"}' }
    end
    return { body = '{"v":1,"ok":true,"cmds":[]}' }
  end
  local M, st = T.agent(w)
  M.cycle(st)
  st.next_cycle = 0
  M.cycle(st)
  local b = T.bodies(w)
  T.ok(by_addr(b[2].machines, w.ebf.addr).sensor, "resent after the refusal")
  T.ok(b[2].ae2.items, "the watchlist too")
  local err = b[2].errors[1]
  T.eq(err.where, "transport")
  T.has(err.msg, "internal")
  M.cycle(st)
  T.eq(#T.last_body(w).errors, 0, "the error was acked by message 2's reply")
end)

test("bad_payload drops what waits, so the next message gets through", function()
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
  M.record_error(st, "machines", "something the core can't read")
  M.cycle(st)
  local b1 = T.bodies(w)[1]
  T.eq(#b1.errors, 1)
  T.ok(by_addr(b1.machines, w.ebf.addr).sensor)
  st.next_cycle = 0
  M.cycle(st)
  local b2 = T.bodies(w)[2]
  T.eq(#b2.errors, 1, "only the refusal itself")
  T.has(b2.errors[1].msg, "bad_payload")
  T.eq(by_addr(b2.machines, w.ebf.addr).sensor, nil, "the refused message's sensor lines are dropped")
  T.eq(b2.ae2.items, nil, "and its watchlist")
  T.eq(#b2.machines, 1, "the snapshot itself still goes")
  T.has(w:printed_text(), "bad_payload", "said on the screen")
  M.cycle(st)
  T.eq(#T.last_body(w).errors, 0)
end)

-- Sensor lines of 32 * 200 bytes, half of them section signs (6 bytes each in the JSON).
local function heavy_lines(tag)
  local out = {}
  for i = 1, 32 do
    local s = ("%s line %02d "):format(tag, i)
    out[i] = (s .. ("\194\167a"):rep(100)):sub(1, 200)
  end
  return out
end

local function sensor_bytes(body)
  local n = 0
  for _, list in ipairs({ body.power, body.machines }) do
    for _, e in ipairs(list) do
      for _, line in ipairs(e.sensor or {}) do
        n = n + #line
      end
    end
  end
  return n
end

test("a message carries at most 8 KB of sensor lines; the rest follow", function()
  local w = T.base({ bare = true, extra = "sensor_per_cycle = 4," })
  local devs = {}
  for i = 1, 6 do
    devs[i] = F.gt_machine(w, { prefix = ("2d%02x"):format(i), name = "multimachine.m" .. i, sensor = heavy_lines("m" .. i) })
  end
  local M, st = T.agent(w)
  T.run_until(M, st, w, 61)
  local first = {}
  for i, body in ipairs(T.bodies(w)) do
    T.ok(sensor_bytes(body) <= 8192, "message " .. i .. " has " .. sensor_bytes(body) .. " bytes of lines")
    for _, m in ipairs(body.machines) do
      if m.sensor then
        T.eq(#m.sensor, 32)
        first[m.addr] = first[m.addr] or i
      end
    end
  end
  for i, d in ipairs(devs) do
    T.ok(first[d.addr], "machine " .. i .. "'s lines went out")
  end
end)

test("an ack never drops sensor lines its message didn't carry", function()
  local w = T.base({ bare = true })
  local a = F.gt_machine(w, { prefix = "2a", name = "multimachine.a" })
  local b = F.gt_machine(w, { prefix = "2b", name = "multimachine.b" })
  local c = F.gt_machine(w, { prefix = "2c", name = "multimachine.c" })
  local M, st = T.agent(w)
  M.discover(st)
  local ea, eb, ec = st.inv.by_addr[a.addr], st.inv.by_addr[b.addr], st.inv.by_addr[c.addr]
  local function lines(e, bytes, seq)
    e.sensor, e.sensor_bytes, e.sensor_at, e.sensor_seq = json.array({ ("x"):rep(bytes) }), bytes, 1, seq
  end
  lines(ec, 4000, 1) -- c went out in message 1, which the core never read (M1 overwrote it)
  lines(ea, 6000, nil) -- then a and b got fresh lines that fill the budget first
  lines(eb, 4000, nil)
  st.seq = 2
  local p = M.build_payload(st)
  T.ok(p.machines[1].sensor, "a fits")
  T.eq(p.machines[2].sensor, nil, "b doesn't")
  T.eq(p.machines[3].sensor, nil, "c doesn't")
  M.ack(st, 2) -- the core read message 2
  T.eq(ea.sensor, nil, "a's lines were in message 2")
  T.ok(eb.sensor, "b's weren't")
  T.ok(ec.sensor, "c's weren't either: message 1's mark no longer counts")
  st.seq = 3
  p = M.build_payload(st)
  T.ok(p.machines[2].sensor and p.machines[3].sensor, "both go in message 3")
  M.ack(st, 3)
  T.eq(eb.sensor, nil)
  T.eq(ec.sensor, nil)
end)

-- The largest base the config allows, with everything waiting and nothing acked: the message
-- the core must accept stays bounded (docs/contracts/oc_bridge.md, Size: about 50 KB; the core
-- takes up to 128 KB).
test("the largest message stays under 64 KB", function()
  local watch = {}
  for i = 1, 64 do
    watch[#watch + 1] = ('    { name = "gregtech:gt.metaitem.%02d", damage = %d },'):format(i, 32000 + i)
  end
  local config = table.concat({
    "{",
    '  token = "' .. T.TOKEN .. '",',
    "  max_machines = 64, max_power = 32, max_watch = 64, max_crafts = 16, sensor_per_cycle = 4,",
    "  watch = {",
    table.concat(watch, "\n"),
    "  },",
    "}",
  }, "\n")
  local w = T.base({ bare = true, config = config })
  local cpus, items, patterns = {}, {}, {}
  for i = 1, 40 do
    cpus[i] = { name = ("cpu %02d "):format(i) .. ("x"):rep(60), storage = 65536 * i, coprocessors = i, busy = i % 2 == 0 }
  end
  for i = 1, 64 do
    items[#items + 1] = { name = ("gregtech:gt.metaitem.%02d"):format(i), damage = 32000 + i, size = 2 ^ 40, isCraftable = true }
    patterns[("gregtech:gt.metaitem.%02d@%d"):format(i, 32000 + i)] = { plan_ticks = 1, craft_ticks = 1 }
  end
  w.me = F.me(w, { prefix = "5e", cpus = cpus, items = items, patterns = patterns })
  for i = 1, 64 do
    F.gt_machine(w, { prefix = ("2%03x"):format(i), name = ("multimachine.some.long.addon.name.%03d"):format(i), sensor = heavy_lines("m" .. i) })
  end
  for i = 1, 32 do
    F.battery_buffer(w, { prefix = ("3%03x"):format(i), sensor = heavy_lines("b" .. i) })
  end
  local id = 0
  w.server = function(req, world)
    -- Never acks; every reply carries crafts and a say without a chat box, so results pile up.
    local cmds = json.array()
    for _ = 1, 8 do
      id = id + 1
      local k = (id % 64) + 1
      if id % 2 == 0 then
        cmds[#cmds + 1] = T.cmd(id, "craft", { name = ("gregtech:gt.metaitem.%02d"):format(k), damage = 32000 + k, amount = 1 })
      else
        cmds[#cmds + 1] = T.cmd(id, "say", { text = ("y"):rep(256) })
      end
    end
    return { body = json.encode({ v = 1, ok = true, cmds = cmds, ack = 0 }) }
  end
  local M, st = T.agent(w)
  for i = 1, 30 do
    M.record_error(st, "machines", ("error %02d "):format(i) .. ("z"):rep(190))
  end
  local biggest = 0
  for _ = 1, 12 do
    st.next_cycle = 0
    M.cycle(st)
    biggest = math.max(biggest, #w.requests[#w.requests].body)
  end
  local last = T.last_body(w)
  T.eq(#last.machines, 64)
  T.eq(#last.power, 32)
  T.eq(#last.ae2.cpus, 32, "crafting CPUs are capped")
  T.eq(#last.ae2.items, 64)
  T.eq(#last.results, 16)
  T.eq(#last.errors, 8)
  T.ok(#last.crafts <= 16, "crafts are capped")
  T.ok(sensor_bytes(last) <= 8192)
  T.ok(biggest <= 64 * 1024, "the biggest message was " .. biggest .. " bytes")
  T.dump("largest-stats.json", json.encode({ biggest_bytes = biggest }))
end)

test("values outside the contract are left out, so the core can always read the message", function()
  local w = T.base({ bare = true })
  local long = F.gt_machine(w, { prefix = "2a", name = ("n"):rep(129) })
  local neg = F.gt_machine(w, { prefix = "2b", name = "multimachine.neg", eu = -5, eu_max = -1 })
  F.plain(w, "odd-type.name", { prefix = "9a" })
  w.me = F.me(w, { prefix = "5e", cpus = { { name = "c", storage = -1, coprocessors = -2, busy = true } } })
  local M, st = T.agent(w)
  M.cycle(st)
  local p = T.last_body(w)
  T.eq(by_addr(p.machines, long.addr).name, nil, "a name over 128 bytes is left out")
  T.eq(by_addr(p.machines, neg.addr).eu, nil, "a negative stored EU is left out")
  T.eq(by_addr(p.machines, neg.addr).eu_max, nil)
  T.eq(p.components["odd-type.name"], nil, "a type name the contract can't carry isn't counted")
  T.eq(p.ae2.cpus[1].storage, 0)
  T.eq(p.ae2.cpus[1].coprocessors, 0)
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
