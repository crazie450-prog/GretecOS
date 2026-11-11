-- Cadence and cost: power and machines every 5 to 10 s, the AE2 watchlist every 60 s by exact
-- lookups only, sensor lines rarely, and a bounded number of server ticks per cycle (WO-7.2,
-- plan 7.9, D213).
local T = require("testlib")
local F = require("fakeoc")
local json = require("json")

local tests = {}
local function test(name, fn)
  tests[#tests + 1] = { name, fn }
end

local function intervals(times)
  local out = {}
  for i = 2, #times do
    out[#out + 1] = times[i] - times[i - 1]
  end
  return out
end

local function request_times(w)
  local out = {}
  for _, r in ipairs(w.requests) do
    out[#out + 1] = r.t
  end
  return out
end

test("ten minutes at the default cadence", function()
  local w = T.base()
  local M, st = T.agent(w)
  T.run_until(M, st, w, 601)
  local times = request_times(w)
  T.between(#times, 118, 122, "messages in 600 s at 5 s")
  for i, d in ipairs(intervals(times)) do
    T.between(d, 4.5, 5.6, "gap before message " .. (i + 1))
  end
  -- The watchlist: one exact getItemInNetwork per watched entry (4) every 60 s, at 1, 61, ...
  -- 541 s (the run stops at 601).
  local lookups = w:times_of("me_controller", "getItemInNetwork")
  T.eq(#lookups, 4 * 10, "four entries, ten reads")
  for i = 5, #lookups do
    T.between(lookups[i] - lookups[i - 4], 59.5, 60.6, "watchlist period")
  end
  T.ok(lookups[4] - lookups[1] < 1, "one read's entries go out together")
  for _, never in ipairs({ "getItemsInNetwork", "allItems", "getItemsInNetworkById", "getCraftables" }) do
    T.eq(w:calls_of("me_controller." .. never), 0, "never " .. never)
  end
  T.eq(w:calls_of("me_controller.getCpus"), #times, "CPUs every cycle")
  -- Sensor lines: each entry about once a minute, at most 2 reads in a cycle.
  local sensor_total = 0
  for _, dev in ipairs({ w.ebf, w.buffer, w.lsc }) do
    local reads = {}
    for _, c in ipairs(w.trace) do
      if c.method == "getSensorInformation" and c.type == dev.type and w.devices[dev.addr] then
        reads[#reads + 1] = c.t
      end
    end
    sensor_total = sensor_total + #reads
  end
  local sensor_calls = w:calls_of("gt_machine.getSensorInformation") + w:calls_of("gt_batterybuffer.getSensorInformation")
  T.between(sensor_calls, 3 * 10, 3 * 11, "3 entries, each every 60 s over 600 s")
  local per_cycle = {}
  for _, c in ipairs(w.trace) do
    if c.method == "getSensorInformation" then
      local cycle = math.floor((c.t - 1) / 5)
      per_cycle[cycle] = (per_cycle[cycle] or 0) + 1
    end
  end
  for cycle, n in pairs(per_cycle) do
    T.ok(n <= 2, "at most sensor_per_cycle reads in cycle " .. cycle .. " (got " .. n .. ")")
  end
  -- Ticks the agent holds per cycle (non-direct calls), for the PR's evidence.
  local per = w.nondirect / #times
  T.ok(per < 16, "non-direct calls per cycle: " .. per)
  T.dump("cadence-stats.json", json.encode({
    seconds = 600,
    messages = #times,
    nondirect_calls = w.nondirect,
    nondirect_per_cycle = math.floor(per * 100 + 0.5) / 100,
    sensor_calls = sensor_calls,
    watchlist_calls = #lookups,
    cpus_calls = w:calls_of("me_controller.getCpus"),
  }))
end)

test("cycle_s = 10", function()
  local w = T.base({ extra = "cycle_s = 10," })
  local M, st = T.agent(w)
  T.run_until(M, st, w, 301)
  local times = request_times(w)
  T.between(#times, 29, 31, "messages in 300 s at 10 s")
  for _, d in ipairs(intervals(times)) do
    T.between(d, 9.5, 10.6, "gap")
  end
  T.eq(#w:times_of("me_controller", "getItemInNetwork"), 4 * 5, "still every 60 s: 1, 61, ... 241")
end)

test("power and machines in every message, cheaply", function()
  local w = T.base()
  local M, st = T.agent(w)
  T.run_until(M, st, w, 31)
  for i, body in ipairs(T.bodies(w)) do
    T.eq(#body.power, 2, "power in message " .. i)
    T.eq(#body.machines, 1, "machines in message " .. i)
    T.eq(body.machines[1].active, true)
    T.eq(body.power[1].eu, 8192, "battery buffer EU (direct call)")
    T.eq(body.power[2].eu_str, "123456789012345678901234", "LSC EU as the exact decimal string")
  end
  -- Machine fields and battery buffer EU are direct calls: no ticks.
  T.eq(w:calls_of("gt_machine.isMachineActive") > 0, true)
  for key in pairs(w.calls) do
    if key:find("isMachineActive") or key:find("getWorkProgress") or key:find("getEUStored") then
      for _, c in ipairs(w.trace) do
        T.no(c.type .. "." .. c.method == key, key .. " must be a direct call")
      end
    end
  end
end)

test("a big base: sensor reads stay rationed", function()
  local w = T.base({ extra = "max_machines = 32," })
  for i = 1, 20 do
    F.gt_machine(w, { prefix = string.format("2f%02x", i), name = "multimachine.machine" .. i })
  end
  local M, st = T.agent(w)
  T.run_until(M, st, w, 301)
  local per_cycle = {}
  local by_addr = {}
  for _, c in ipairs(w.trace) do
    if c.method == "getSensorInformation" then
      local cycle = math.floor((c.t - 1) / 5)
      per_cycle[cycle] = (per_cycle[cycle] or 0) + 1
    end
  end
  for cycle, n in pairs(per_cycle) do
    T.ok(n <= 2, "cycle " .. cycle .. " read " .. n .. " sensors")
  end
  -- 23 entries at 2 a cycle: everyone is read within 12 cycles.
  for i, body in ipairs(T.bodies(w)) do
    for _, m in ipairs(body.machines) do
      if m.sensor then
        by_addr[m.addr] = by_addr[m.addr] or i
      end
    end
  end
  local n = 0
  for _, first in pairs(by_addr) do
    n = n + 1
    T.ok(first <= 12, "first sensor lines by message 12")
  end
  T.eq(n, 21, "every machine's lines arrived")
end)

test("while the core is down, no sensor or watchlist reads", function()
  local w = T.base()
  w.server = function()
    return { fail = "Connection refused (Connection refused)" }
  end
  local M, st = T.agent(w)
  T.run_until(M, st, w, 200)
  local sensor_after_first = 0
  for _, c in ipairs(w.trace) do
    if c.method == "getSensorInformation" and c.t > 2 then
      sensor_after_first = sensor_after_first + 1
    end
  end
  T.eq(sensor_after_first, 0, "sensor reads wait for the link")
  T.eq(#w:times_of("me_controller", "getItemInNetwork"), 4, "only the first cycle's watchlist read")
  -- Backoff: 5, 10, 20, 40, 60, 60 ...
  local gaps = intervals(request_times(w))
  T.near(gaps[1], 5, 0.6, "first retry")
  T.near(gaps[2], 10, 0.6)
  T.near(gaps[3], 20, 0.6)
  T.near(gaps[4], 40, 0.6)
  T.near(gaps[5], 60, 0.6, "capped at backoff_max_s")
end)

return tests
