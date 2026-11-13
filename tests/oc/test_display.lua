-- The in-game displays, oc/display.lua (WO-7.4; plan 7.9, D277-D279): the status board, the
-- OCGlasses HUD and chat notices, drawn from the reply's `display` over both transports, against
-- fakeoc's screen, GPU, glasses terminal and chat box.
local T = require("testlib")
local F = require("fakeoc")
local json = require("json")

local tests = {}
local function test(name, fn)
  tests[#tests + 1] = { name, fn }
end

local DIR = "/home/gos/mailbox"
local OUTBOX, TMP, INBOX = DIR .. "/outbox.json", DIR .. "/outbox.tmp", DIR .. "/inbox.json"
local GOLDEN_REPLY = F.ROOT .. "/tests/fixtures/oc/reply-display.json"

-- A fake core's display, computed from each message as the real one is from telemetry: one line
-- per machine (stalled when it has work, is allowed and isn't running), the LSC's stored EU, and
-- any notices queued in c.chat. rev is a counter that moves only when the lines change.
local function display_core(w, c)
  c = c or {}
  c.rev, c.last, c.chat, c.sent = 0, nil, c.chat or {}, {}
  function c.respond(doc)
    local lines = {}
    for _, p in ipairs(doc.power or {}) do
      if p.kind == "storage" then
        lines[#lines + 1] = { t = "PWR  LSC " .. tostring(p.eu_str), c = "ok" }
      end
    end
    for _, m in ipairs(doc.machines or {}) do
      local stalled = m.work and m.allowed and not m.active
      lines[#lines + 1] = {
        t = ("!    %s: %s"):format(m.name, stalled and "stalled" or (m.allowed == false and "paused" or "ok")),
        c = stalled and "warn" or "ok",
      }
    end
    lines[#lines + 1] = { t = "NEXT Bronze Age", c = "info" }
    local key = json.encode(lines)
    if key ~= c.last then
      c.rev, c.last = c.rev + 1, key
    end
    local reply = { v = 1, ok = true, cmds = json.array() }
    local shows = doc.display
    if shows then
      local chat = {}
      for _, n in ipairs(c.chat) do
        if n.id > shows.said and #chat < 2 then
          chat[#chat + 1] = n
        end
      end
      if shows.rev ~= c.rev or #chat > 0 then
        reply.display = { rev = c.rev }
        if shows.board then
          reply.display.lines = lines
        end
        if shows.hud then
          reply.display.hud = { { t = "GOS PWR 12.3%", c = "ok" }, lines[2] and { t = lines[2].t:sub(1, 40), c = lines[2].c } or nil }
        end
        if #chat > 0 then
          reply.display.chat = chat
        end
      end
    end
    c.sent[#c.sent + 1] = reply
    return reply
  end
  return c
end

local function http_world(opts)
  opts = opts or {}
  local w = T.base({ screen = opts.screen or { w = 80, h = 25 }, glasses = opts.glasses, extra = opts.extra })
  local c = display_core(w, opts.core)
  w.server = function(req, world)
    local reply = c.respond(assert(json.decode(req.body)))
    local body = json.encode(reply)
    if not c.invalid then -- a test that sends what the schema refuses on purpose says so
      world.valid_replies[#world.valid_replies + 1] = body
    end
    return { body = body }
  end
  return w, c
end

-- The mailbox's far side: every 0.25 s take outbox.json (renamed back to outbox.tmp), answer at
-- once in inbox.json while there is none, as gregtec.bridge.mailbox does.
local function mailbox_world(opts)
  opts = opts or {}
  local w = T.base({
    no_internet = true,
    screen = opts.screen or { w = 80, h = 25 },
    glasses = opts.glasses,
    extra = 'transport = "mailbox",' .. (opts.extra or ""),
  })
  local c = display_core(w, opts.core)
  local seq = {}
  local function tick(world)
    local body = world.files[OUTBOX]
    if body and not world.files[INBOX] then
      world.files[TMP], world.files[OUTBOX] = body, nil
      if world.on_request then
        world.on_request(body)
      end
      local doc = assert(json.decode(body))
      seq.boot, seq.n = doc.boot, math.max(seq.boot == doc.boot and seq.n or 0, doc.seq)
      local reply = c.respond(doc)
      reply.boot, reply.ack = seq.boot, seq.n
      local text = json.encode(reply)
      if not c.invalid then
        world.valid_replies[#world.valid_replies + 1] = text
      end
      world.files[INBOX] = text
      world.mtimes[INBOX] = world.clock * 1000
    end
    world:at(world.clock + 0.25, tick)
  end
  w:at(w.clock + 0.25, tick)
  return w, c
end

-- The agent as M.run starts it: transport, then displays.
local function agent(w, opts)
  local M, st = T.agent(w, opts)
  M.open_display(st)
  M.discover(st)
  return M, st
end

local function rows(w)
  local out = {}
  for y = 1, w.screen.res_h do
    out[y] = w.screen.rows[y] or ""
  end
  return out
end

local function board_has(w, text)
  for _, r in ipairs(rows(w)) do
    if r:find(text, 1, true) then
      return true
    end
  end
  return false
end

test("options: defaults, false, unknown keys and bad values", function()
  local w = T.base()
  F.load_agent(w)
  local D = dofile(F.ROOT .. "/oc/display.lua")
  local cfg = assert(D.check(true))
  T.same(cfg, { board = true, screen = false, hud = true, chat = true })
  T.same(assert(D.check(false)), { board = false, screen = false, hud = false, chat = false })
  T.same(assert(D.check({ hud = false })).hud, false)
  local _, err = D.check({ colour = "red" })
  T.has(err, "unknown option colour")
  _, err = D.check({ board = "yes" })
  T.has(err, "board must be true or false")
  _, err = D.check({ screen = "ab" })
  T.has(err, "screen must be false")
  _, err = D.check("on")
  T.has(err, "display must be true, false or a table")
end)

test("a bad display option is a config error that stops the agent", function()
  local w = http_world({ extra = "display = { board = 1 }," })
  local M, st = T.agent(w)
  local ok, err = M.run(st, { once = true })
  T.no(ok)
  T.has(err, "display: board must be true or false")
end)

test("the board fits OC's smallest screen and draws the core's lines", function()
  for _, case in ipairs({ { 50, 16 }, { 80, 25 }, { 160, 50 } }) do
    local w = http_world({ screen = { w = case[1], h = case[2] } })
    local M, st = agent(w)
    T.ok(M.cycle(st))
    T.eq(w.screen.res_w, 50, "width on a " .. case[1] .. "x" .. case[2] .. " screen")
    T.eq(w.screen.res_h, 16, "height")
    local s = st.display:state()
    T.same(s.board, { w = 50, h = 16 })
    local r = rows(w)
    T.has(r[1], "GREGTEC OS")
    T.has(r[1], "M2 0s")
    T.has(r[2], "PWR  LSC 123456789012345678901234")
    T.has(r[3], "!    multimachine.blastfurnace: ok")
    T.has(r[4], "NEXT Bronze Age")
    for y, text in ipairs(r) do
      T.eq(#text, 50, "row " .. y .. " is exactly the width")
    end
    T.eq(w.screen.overflow or 0, 0, "nothing was drawn past the edge")
  end
end)

test("close gives the shell back its resolution", function()
  local w = http_world({ screen = { w = 80, h = 25 } })
  T.eq(w.screen.res_w, 80)
  local M, st = agent(w)
  T.ok(M.cycle(st))
  T.eq(w.screen.res_w, 50)
  st.display:close()
  T.eq(w.screen.res_w, 80, "width after close")
  T.eq(w.screen.res_h, 25, "height after close")
end)

test("lines are cut to the width and drawn as text, never run", function()
  local w = T.base({ screen = { w = 50, h = 16 } })
  local M, st = agent(w)
  local evil = 'os.execute("rm -rf /") ' .. string.rep("x", 80) .. "\1\27[2J"
  st.display:apply({ rev = 7, lines = { { t = evil, c = "bad" }, { t = 42, c = "nope" } } })
  local r = rows(w)
  T.eq(#r[2], 50)
  T.eq(r[2]:sub(1, 24), 'os.execute("rm -rf /") x')
  T.no(r[2]:find("[%c]"), "control characters are not drawn")
  T.eq(w.screen.fg[2], 0xFF5555, "bad is red")
  T.eq(r[3], string.rep(" ", 50), "a line that isn't text is blank")
  T.eq(w.screen.fg[3], 0xFFFFFF, "an unknown level is drawn as info")
end)

test("only the rows that changed are written again", function()
  local w = T.base({ screen = { w = 50, h = 16 } })
  local M, st = agent(w)
  local lines = {}
  for i = 1, 12 do
    lines[i] = { t = "line " .. i, c = "info" }
  end
  st.display:apply({ rev = 1, lines = lines })
  local before = w.gpu.sets
  lines[5] = { t = "line 5 changed", c = "warn" }
  st.display:apply({ rev = 2, lines = lines })
  T.eq(w.gpu.sets - before, 1, "one row rewritten")
  before = w.gpu.sets
  st.display:apply({ rev = 2, lines = lines })
  T.eq(w.gpu.sets - before, 0, "the same rev draws nothing")
end)

test("a board with no reply for 30 s says so and greys the old lines", function()
  local w, c = http_world({ screen = { w = 50, h = 16 } })
  local M, st = agent(w)
  T.ok(M.cycle(st))
  T.eq(w.screen.fg[3], 0x55FF55, "fresh: the machine's line is ok green")
  w.server = function()
    return { fail = "Connection refused" }
  end
  local r, fg
  w:at(w.clock + 44, function(world)
    r, fg = rows(world), { table.unpack(world.screen.fg, 1, world.screen.res_h) } -- before the stop blanks it
  end)
  T.run_until(M, st, w, w.clock + 45)
  T.has(r[1], "NO REPLY")
  T.eq(fg[1], 0xFF5555, "the header is red")
  T.eq(fg[3], 0x888888, "the old line is grey")
  T.has(r[3], "multimachine.blastfurnace: ok", "still there, marked, never removed or guessed")
  T.has(r[16], "transport", "the footer has the agent's newest note")
end)

test("while the board has the screen the agent's notes go to its footer, not the terminal", function()
  local w = http_world({ screen = { w = 50, h = 16 } })
  local M, st = T.agent(w)
  w.printed = {}
  T.run_until(M, st, w, 12)
  T.eq(#w.printed, 1, "only the stop line, after the board gave the screen back: " .. w:printed_text())
  T.has(w.printed[1], "stopped")
  T.eq(w.screen.rows[2], string.rep(" ", 50), "the board was blanked on stop")
end)

test("no screen or GPU that answers: no board, and the notes stay on the terminal", function()
  local w = T.base() -- plain screen and gpu: no methods
  local M, st = agent(w)
  T.no(st.display:board_on())
  T.eq(st.display:state().board, nil)
  M.log(st, "info", "hello")
  T.has(w:printed_text(), "hello")
end)

test("display = false: no displays and no display field", function()
  local w = http_world({ extra = "display = false," })
  local M, st = agent(w)
  T.eq(st.display, nil)
  T.ok(M.cycle(st))
  T.eq(T.last_body(w).display, nil)
end)

test("without display.lua the agent still runs, says why, and sends no display", function()
  local w = http_world()
  local M, st = T.agent(w)
  local hidden = F.ROOT .. "/oc/display.lua"
  local loadfile0 = loadfile
  loadfile = function(path, ...) -- luacheck: ignore
    if path == hidden then
      return nil, "cannot open " .. path
    end
    return loadfile0(path, ...)
  end
  local ok = pcall(M.open_display, st)
  loadfile = loadfile0 -- luacheck: ignore
  T.ok(ok)
  T.eq(st.display, nil)
  T.has(w:printed_text(), "display.lua is not installed")
  T.ok(M.cycle(st))
  T.eq(T.last_body(w).display, nil)
end)

test("the HUD: M2 only, a fixed set of widgets, only changed lines set", function()
  local w, c = http_world({ glasses = true })
  local M, st = agent(w)
  T.ok(M.cycle(st))
  T.eq(st.display:state().hud, true)
  T.eq(w.glasses:energy_per_tick(), 0.4, "4 widgets: 0.4 OC energy a tick [F 4.1]")
  local texts = {}
  for id, wd in pairs(w.glasses.widgets) do
    texts[id] = wd.text
  end
  T.eq(texts[1], "GOS PWR 12.3%")
  T.ok(#texts[2] <= 40)
  local packets = w.glasses.packets
  st.next_cycle = 0
  T.ok(M.cycle(st))
  T.eq(w.glasses.packets, packets, "nothing changed: no packet")
  w.ebf.active = false
  st.next_cycle = 0
  T.ok(M.cycle(st))
  T.eq(w.glasses.packets - packets, 2, "one line changed: its text and colour, one batch")
  T.has(w.glasses.widgets[2].text, "stalled")
  T.eq(w.glasses:energy_per_tick(), 0.4, "still 4 widgets")
  M.run(st, { once = true })
  T.eq(w.glasses:energy_per_tick(), 0, "removed when the agent stops")
end)

test("no HUD over the mailbox (M1), even with a glasses terminal", function()
  local w = mailbox_world({ glasses = true })
  local M, st = agent(w)
  T.ok(M.cycle(st))
  T.eq(st.display:state().hud, false)
  T.eq(w.glasses:energy_per_tick(), 0)
  T.ok(board_has(w, "M1"))
end)

test("chat notices: each said once, in order, at most two a reply, said reported back", function()
  local w, c = http_world({
    core = {
      invalid = true, -- the third notice has bytes the schema refuses: the agent must cope
      chat = {
        { id = 1001, t = "GOS: Confirmed: Craft 64 Steel Ingot" },
        { id = 1002, t = "GOS: Done: Craft 64 Steel Ingot" },
        { id = 1003, t = "GOS: Failed: \1Craft 1 Diamond \194\167cmissing" },
      },
    },
  })
  local M, st = agent(w)
  T.ok(M.cycle(st))
  T.same(w.chat.said, { "GOS: Confirmed: Craft 64 Steel Ingot", "GOS: Done: Craft 64 Steel Ingot" })
  st.next_cycle = 0
  T.ok(M.cycle(st))
  T.eq(T.last_body(w).display.said, 1002, "the next message says what was said")
  T.eq(#w.chat.said, 3)
  T.eq(w.chat.said[3], "GOS: Failed: ?Craft 1 Diamond ??cmissing", "control and non-ASCII bytes are not said")
  for _ = 1, 3 do
    st.next_cycle = 0
    T.ok(M.cycle(st))
  end
  T.eq(#w.chat.said, 3, "never twice")
end)

-- Plan 8 WO-7.4 acceptance on the fake clock: a change in the world reaches the board (and the
-- HUD) within 10 s, over both transports, with the default 5 s cycle.
local function reaches_within(make, label)
  local w, c = make()
  local M, st = T.agent(w)
  local seen = {}
  local function watch(world)
    if not seen.stalled and board_has(world, "stalled") then
      seen.stalled = world.clock
    end
    if not seen.back and seen.stalled and board_has(world, "blastfurnace: ok") then
      seen.back = world.clock
    end
    world:at(world.clock + 0.05, watch)
  end
  w:at(w.clock, watch)
  local stop_at, back_at = 33.3, 61.7
  w:at(stop_at, function()
    w.ebf.active = false
  end)
  w:at(back_at, function()
    w.ebf.active = true
  end)
  T.run_until(M, st, w, 90)
  T.ok(seen.stalled, label .. ": the stall reached the board")
  T.between(seen.stalled - stop_at, 0, 10, label .. ": seconds from the stall to the board")
  T.ok(seen.back, label .. ": the recovery reached the board")
  T.between(seen.back - back_at, 0, 10, label .. ": seconds from the recovery to the board")
  return w, c, seen
end

test("a change reaches the board within 10 s over HTTP (M2)", function()
  reaches_within(function()
    return http_world({ glasses = true })
  end, "M2")
end)

test("a change reaches the board within 10 s over the mailbox (M1)", function()
  reaches_within(mailbox_world, "M1")
end)

test("the same reply draws the same board over both transports", function()
  local function board_after(make)
    local w = make({ screen = { w = 50, h = 16 } })
    local M, st = agent(w)
    T.ok(M.cycle(st))
    local r = rows(w)
    table.remove(r, 1) -- the header names the transport
    table.remove(r) -- the footer the agent's newest note (its byte count)
    return r
  end
  T.same(board_after(mailbox_world), board_after(http_world))
end)

-- Golden: the telemetry with a display (tests/fixtures/oc/telemetry-display.json) and the reply
-- the core builds for it (reply-display.json, which tests/unit/test_oc_display.py rebuilds with
-- the real Python builder), drawn on a tier-1 screen.
test("golden display", function()
  local w = T.base({ screen = { w = 50, h = 16 }, glasses = true })
  w.ebf.active = false -- stalled: it has a job, work is allowed, and it isn't running
  local found = io.open(GOLDEN_REPLY, "rb")
  local reply_text = found and found:read("a") or '{"v":1,"ok":true,"cmds":[]}'
  if found then
    found:close()
  end
  w.server = function(req, world)
    world.valid_replies[#world.valid_replies + 1] = json.encode(assert(json.decode(reply_text)))
    return { body = reply_text }
  end
  local M, st = agent(w)
  T.ok(M.cycle(st))
  T.dump("golden-telemetry-display.json", w.requests[1].body)
  T.ok(found, "tests/fixtures/oc/reply-display.json is there (tests/unit/test_oc_display.py makes it)")
  T.dump("golden-reply-display.json", json.encode(assert(json.decode(reply_text))))
  local reply = assert(json.decode(reply_text))
  local r = rows(w)
  for i, ln in ipairs(reply.display.lines) do
    T.eq(r[i + 1]:sub(1, #ln.t), ln.t, "board row " .. (i + 1))
  end
  T.eq(st.display.rev, reply.display.rev)
  for i, ln in ipairs(reply.display.hud) do
    T.eq(w.glasses.widgets[i].text, ln.t, "HUD line " .. i)
  end
  T.eq(#w.chat.said, #reply.display.chat)
  st.next_cycle = 0
  T.ok(M.cycle(st))
  local shows = T.last_body(w).display
  T.eq(shows.rev, reply.display.rev)
  T.eq(shows.said, reply.display.chat[#reply.display.chat].id)
end)

return tests
