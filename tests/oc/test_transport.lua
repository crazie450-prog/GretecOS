-- The M2 HTTP transport: the POST, the reply read in 2 KB chunks, and every error path closing
-- its connection and retrying with backoff (WO-7.2).
local T = require("testlib")
local F = require("fakeoc")
local json = require("json")

local tests = {}
local function test(name, fn)
  tests[#tests + 1] = { name, fn }
end

local function last_error(st)
  return st.errors[#st.errors]
end

local OK = '{"v":1,"ok":true,"cmds":[]}'

-- A valid reply of exactly n bytes (padded in err, which ok:true replies may carry).
local function reply_of_size(n)
  local head, tail = '{"v":1,"ok":true,"cmds":[],"err":"', '"}'
  return head .. string.rep("p", n - #head - #tail) .. tail
end

test("the request: POST, JSON, the bearer token, the configured url", function()
  local w = T.base({ extra = 'url = "http://127.0.0.1:9123/oc/telemetry",' })
  local M, st = T.agent(w)
  T.ok(M.cycle(st))
  local req = w.requests[1]
  T.eq(req.url, "http://127.0.0.1:9123/oc/telemetry")
  T.eq(req.method, "POST")
  T.eq(req.headers["Content-Type"], "application/json")
  T.eq(req.headers["Authorization"], "Bearer " .. T.TOKEN)
  T.ok(json.decode(req.body), "the body is JSON")
  T.ok(req.closed, "the connection is closed")
  T.eq(w.card.nopen(), 0)
end)

test("a reply in small pieces with waits in between", function()
  local w = T.base()
  local body = json.encode({ v = 1, ok = true, cmds = json.array({ T.cmd(1, "say", { text = "caf\195\169 is open" }) }) })
  w.server = function()
    return { body = body, pieces = { 1, 7, 100 }, gaps = 3 }
  end
  local M, st = T.agent(w)
  T.ok(M.cycle(st))
  T.eq(w.chat.said[1], "caf\195\169 is open", "the reply's \\u escape arrives as UTF-8")
  T.eq(#body, 108)
  T.eq(w.requests[1].reads, 3, "the three pieces, each its own read (the empty reads in between not counted)")
  T.eq(w.requests[1].max_read, 2048, "never more than 2048 bytes a read")
end)

test("2048 bytes is one read, 2049 two", function()
  for _, case in ipairs({ { 2048, 1 }, { 2049, 2 }, { 4096, 2 }, { 6000, 3 } }) do
    local w = T.base()
    w.server = function()
      return { body = reply_of_size(case[1]) }
    end
    local M, st = T.agent(w)
    T.ok(M.cycle(st), case[1] .. " bytes")
    T.eq(w.requests[1].reads, case[2], "reads for " .. case[1] .. " bytes")
  end
end)

test("a reply over reply_max is refused and closed", function()
  local w = T.base()
  w.server = function()
    local r = { v = 1, ok = true, cmds = json.array({ T.cmd(1, "say", { text = "never" }) }), err = string.rep("x", 9000) }
    return { body = json.encode(r) }
  end
  local M, st = T.agent(w)
  T.no(M.cycle(st))
  T.has(last_error(st).msg, "reply longer than 8192 bytes")
  T.eq(#w.chat.said, 0, "no command from a refused reply")
  T.eq(w.card.nopen(), 0)
end)

test("connection refused, HTTP 500, odd codes, bad JSON", function()
  local cases = {
    { { fail = "Connection refused (Connection refused)" }, "Connection refused" },
    { { fail = "Server returned HTTP response code: 500 for URL: http://127.0.0.1:8765/oc/telemetry" }, "response code: 500" },
    { { fail = "address is not allowed" }, "address is not allowed" },
    { { code = 204, body = "" }, "HTTP 204" },
    { { body = "<html>proxy error</html>" }, "bad reply: json" },
    { { body = "[1,2]" }, "bad reply: not an object" },
    { { body = '{"v":1,"ok":true' }, "bad reply: json" },
    { { body = '{"v":2,"ok":true,"cmds":[]}' }, "contract v2" },
  }
  for _, c in ipairs(cases) do
    local w = T.base()
    w.server = function()
      return c[1]
    end
    local M, st = T.agent(w)
    T.no(M.cycle(st), c[2])
    T.has(last_error(st).msg, c[2])
    T.eq(last_error(st).where, "transport")
    T.eq(w.card.nopen(), 0, "closed after " .. c[2])
    T.eq(st.failures, 1)
  end
end)

test("a core that never answers: the exchange gives up in time", function()
  for _, spec in ipairs({ { stall_connect = true, body = OK }, { stall_read = true, body = OK } }) do
    local w = T.base()
    w.server = function()
      return spec
    end
    local M, st = T.agent(w)
    local t0 = w.clock
    T.no(M.cycle(st))
    T.between(w.clock - t0, 12, 14, "request_timeout_s")
    T.has(last_error(st).msg, spec.stall_connect and "no answer from the core in 12 s" or "stalled for 12 s")
    T.eq(w.card.nopen(), 0)
  end
end)

test("unauthorized: wait the longest backoff, log once", function()
  local w = T.base()
  w.server = function()
    return { body = '{"v":1,"ok":false,"cmds":[],"err":"unauthorized"}' }
  end
  local M, st = T.agent(w)
  T.run_until(M, st, w, 400)
  local gaps = {}
  for i = 2, #w.requests do
    gaps[#gaps + 1] = w.requests[i].t - w.requests[i - 1].t
  end
  for _, g in ipairs(gaps) do
    T.between(g, 59.5, 60.6, "60 s between tries")
  end
  T.has(last_error(st).msg, "unauthorized")
  local logged = 0
  for line in (w.files["/home/gos/gos-agent.log"] or ""):gmatch("[^\n]+") do
    if line:find("unauthorized", 1, true) then
      logged = logged + 1
    end
  end
  T.between(logged, 1, 2, "logged at most once in 5 minutes")
end)

test("no internet access: the reason comes through", function()
  local w = T.base()
  w.inet_unavailable = true
  local M, st = T.agent(w)
  T.no(M.cycle(st))
  T.has(last_error(st).msg, "internet access is unavailable")
end)

test("other programs holding the card's 4 connections", function()
  local w = T.base()
  for _ = 1, 4 do
    w.card.open[{}] = true
  end
  local M, st = T.agent(w)
  T.no(M.cycle(st))
  T.has(last_error(st).msg, "too many open connections")
  for h in pairs(w.card.open) do
    w.card.open[h] = nil
  end
  st.next_cycle = 0
  T.ok(M.cycle(st), "works once they are free")
end)

test("never leaks a connection, whatever fails", function()
  local w = T.base()
  local specs = {
    { fail = "Connection refused" },
    { stall_connect = true },
    { stall_read = true, body = OK },
    { body = "garbage" },
    { body = reply_of_size(9000) },
    { code = 302, body = OK },
    { body = OK, pieces = { 3, 3, 3 }, gaps = 2 },
  }
  local n = 0
  w.server = function()
    n = n + 1
    return specs[(n - 1) % #specs + 1]
  end
  local M, st = T.agent(w)
  for _ = 1, 28 do
    st.next_cycle = 0
    M.cycle(st)
    T.eq(w.card.nopen(), 0, "open connections after exchange " .. n)
  end
  for _, e in ipairs(st.errors) do
    T.no(e.msg:find("too many open connections", 1, true), "never hit OC's connection limit")
  end
end)

test("recovery resets the backoff", function()
  local w = T.base()
  local up_at = 60
  w.server = function(_, world)
    if world.clock < up_at then
      return { fail = "Connection refused" }
    end
    return { body = OK }
  end
  local M, st = T.agent(w)
  T.run_until(M, st, w, 130)
  local times = {}
  for _, r in ipairs(w.requests) do
    times[#times + 1] = r.t
  end
  local after = {}
  for i = 2, #times do
    if times[i - 1] >= up_at then
      after[#after + 1] = times[i] - times[i - 1]
    end
  end
  T.ok(#after >= 5, "back to the normal cadence")
  for _, g in ipairs(after) do
    T.between(g, 4.5, 5.6, "5 s once the core answers")
  end
  T.has(w.files["/home/gos/gos-agent.log"], "the core answers again")
end)

test("poll_s asks for the next message sooner", function()
  local w = T.base()
  w.server = function()
    return { body = '{"v":1,"ok":true,"cmds":[],"poll_s":2}' }
  end
  local M, st = T.agent(w)
  T.run_until(M, st, w, 21)
  for i = 2, #w.requests do
    T.between(w.requests[i].t - w.requests[i - 1].t, 1.5, 2.6, "every 2 s (give or take the sampling ticks)")
  end
end)

test("auto without an internet card and without the mailbox stops with a clear reason", function()
  local w = T.base({ no_internet = true })
  local M = assert(F.load_agent(w))
  local lines = {}
  local code = M.main(table.pack("/home/gos/gos-agent.cfg"), { out = function(s)
    lines[#lines + 1] = s
  end })
  T.eq(code, 1)
  T.has(table.concat(lines, "\n"), "mailbox.lua, WO-7.3")
  T.eq(#w.requests, 0)
end)

test("the mailbox transport slot (WO-7.3) is used when mailbox.lua is there", function()
  local w = T.base({ no_internet = true, extra = 'transport = "mailbox", mailbox = { dir = "/home/gos/mailbox" },' })
  local M = assert(F.load_agent(w))
  local cfg = assert(M.load_config("/home/gos/gos-agent.cfg"))
  local st = M.new_state(cfg, { out = function() end, boot = "b0075eed" })
  -- A stand-in for WO-7.3's module: the interface the agent relies on.
  local sent = {}
  st.transport = {
    name = "mailbox",
    implicit_ack = false,
    exchange = function(self, body, seq)
      sent[#sent + 1] = { body = body, seq = seq }
      return { v = 1, ok = true, cmds = json.array(), ack = seq > 1 and seq - 1 or nil }
    end,
    close = function() end,
  }
  M.cycle(st)
  M.cycle(st)
  M.cycle(st)
  T.eq(#sent, 3)
  local b1, b2, b3 = json.decode(sent[1].body), json.decode(sent[2].body), json.decode(sent[3].body)
  T.eq(b1.transport, "mailbox")
  T.ok(b2.machines[1].sensor, "no ack yet for message 1: its lines go again")
  T.eq(b3.machines[1].sensor, nil, "acked once the core has message 2")
end)

return tests
