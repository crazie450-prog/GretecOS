-- The M1 file-mailbox transport, oc/mailbox.lua (WO-7.3, plan 7.9, research F§2, D216): the
-- agent against a fake core on the far side of the folder that behaves as gregtec.bridge.mailbox
-- does.
local T = require("testlib")
local json = require("json")

local tests = {}
local function test(name, fn)
  tests[#tests + 1] = { name, fn }
end

local DIR = "/home/gos/mailbox"
local OUTBOX, TMP, INBOX = DIR .. "/outbox.json", DIR .. "/outbox.tmp", DIR .. "/inbox.json"
local MAILBOX = 'transport = "mailbox",'

-- A fake core, as gregtec.bridge.mailbox behaves: every poll_s it takes outbox.json (reads it,
-- then renames it back to outbox.tmp; from outside, so the drive's space count never sees it),
-- dedupes by (computer, boot, seq), and writes inbox.json at once (as os.replace does), only
-- while there is none. A reply OC hasn't read in ttl_s is withdrawn and its commands returned.
--   c.down          not polling (the core isn't running)
--   c.delay         seconds between seeing outbox.json and taking it
--   c.queue         confirmed commands to send, at most 8 a reply
--   c.raw           the next reply's exact text (a bad inbox), instead of a real reply
--   c.rename_to     take outbox.json by renaming it to this name instead (the space-count contrast)
local function core(w, opts)
  opts = opts or {}
  local c = {
    poll_s = opts.poll_s or 0.25,
    ttl_s = opts.ttl_s or 30,
    delay = opts.delay or 0,
    down = opts.down or false,
    queue = {},
    taken = {}, -- decoded messages, in the order they were taken
    replies = {}, -- every reply written
    withdrawn = {}, -- command ids handed back
    withdrawn_replies = 0,
    seen = {}, -- computer address -> { boot = , seq = }
    repeats = 0,
    consumed = 0,
    due = false,
  }
  function c.poll(world)
    local body = world.files[OUTBOX]
    if body then
      c.seen_at = c.seen_at or world.clock
      if world.clock - c.seen_at >= c.delay then
        c.seen_at = nil
        local name = c.rename_to or TMP
        world.files[name], world.files[OUTBOX] = body, nil
        local doc = assert(json.decode(body), "the core reads a whole message")
        if world.on_request then
          world.on_request(body) -- the runner dumps it for the schema check
        end
        local s = c.seen[doc.computer.address]
        if s and s.boot == doc.boot and doc.seq <= s.seq then
          c.repeats = c.repeats + 1
        else
          if not s or s.boot ~= doc.boot then
            s = { boot = doc.boot, seq = 0 }
            c.seen[doc.computer.address] = s
          end
          s.seq = math.max(s.seq, doc.seq)
          c.taken[#c.taken + 1] = doc
        end
        c.latest = { boot = s.boot, seq = s.seq }
        c.due = true
      end
    end
    if world.files[INBOX] then
      if c.reply_at and world.clock - c.reply_at >= c.ttl_s then
        world.files[INBOX] = nil
        c.withdrawn_replies = c.withdrawn_replies + 1
        for _, id in ipairs(c.reply_ids) do
          c.withdrawn[#c.withdrawn + 1] = id
        end
        c.reply_at, c.reply_ids = nil, nil
      end
      return
    end
    if c.reply_at then
      c.consumed = c.consumed + 1
      c.reply_at, c.reply_ids = nil, nil
    end
    if not c.due then
      return
    end
    c.due = false
    local text
    if c.raw then
      text, c.raw = c.raw, nil
    else
      local cmds, ids = json.array(), {}
      while #cmds < 8 and #c.queue > 0 do
        local cmd = table.remove(c.queue, 1)
        cmds[#cmds + 1] = cmd
        ids[#ids + 1] = cmd.id
      end
      local reply = { v = 1, ok = true, cmds = cmds, boot = c.latest.boot, ack = c.latest.seq }
      text = json.encode(reply)
      c.reply_ids = ids
      world.valid_replies[#world.valid_replies + 1] = text
    end
    c.replies[#c.replies + 1] = text
    world.files[INBOX] = text
    world.mtimes[INBOX] = world.clock * 1000
    c.reply_at = world.clock
  end
  local function tick(world)
    if not c.down then
      c.poll(world)
    end
    world:at(world.clock + c.poll_s, tick)
  end
  w:at(w.clock + c.poll_s, tick)
  return c
end

local function base(opts)
  opts = opts or {}
  local w = T.base({ no_internet = true, extra = MAILBOX .. (opts.extra or "") })
  local c = core(w, opts.core)
  local M, st = T.agent(w, { boot = opts.boot })
  return w, c, M, st
end

local function last_error(st)
  return st.errors[#st.errors]
end

local function count_lists(w)
  local n = { list = 0 }
  local list = w.filesystem.list
  w.filesystem.list = function(...)
    n.list = n.list + 1
    return list(...)
  end
  return n
end

-- The command ids whose results reached the core, each counted once.
local function results_at_core(c)
  local ids = {}
  for _, doc in ipairs(c.taken) do
    for _, r in ipairs(doc.results) do
      ids[r.id] = (ids[r.id] or 0) + 1
    end
  end
  return ids
end

test("a round trip: outbox.tmp renamed to outbox.json, the reply read and deleted, acked", function()
  local w, c, M, st = base()
  local lists = count_lists(w)
  T.ok(M.cycle(st))
  T.eq(#c.taken, 1)
  T.eq(c.taken[1].seq, 1)
  T.eq(c.taken[1].transport, "mailbox")
  T.eq(w.files[OUTBOX], nil, "the core took it")
  T.ok(w.files[TMP], "and renamed it back to outbox.tmp")
  T.eq(w.files[INBOX], nil, "the reply was read and deleted")
  T.eq(st.transport.answered, true)
  T.eq(st.transport.n.written, 1)
  T.eq(st.transport.n.replies, 1)
  T.eq(st.machines, nil)
  T.eq(st.inv.machines[1].sensor, nil, "the reply's ack (boot and seq 1) dropped the sensor lines it carried")
  T.eq(w:calls_of("filesystem.rename"), 1, "one rename")
  T.eq(w:calls_of("filesystem.remove"), 1, "one remove")
  T.eq(lists.list, 0, "never list (a tick each)")
  T.ok(w.clock - 1 < 2, "the reply came within the exchange's wait")
  -- The next message goes over the old outbox.tmp.
  st.next_cycle = 0
  T.ok(M.cycle(st))
  T.eq(#c.taken, 2)
  T.eq(c.taken[2].seq, 2)
end)

test("the inbox is read in 2 KB chunks, then deleted", function()
  for _, case in ipairs({ { 2048, 1 }, { 2049, 2 }, { 5000, 3 } }) do
    local w, c, M, st = base()
    local head, tail = '{"v":1,"ok":true,"cmds":[],"err":"', '"}'
    c.raw = head .. string.rep("p", case[1] - #head - #tail) .. tail
    T.ok(M.cycle(st), case[1] .. " bytes")
    T.eq(w.fs_reads[INBOX], case[2] + 1, "reads for " .. case[1] .. " bytes (and the one that finds the end)")
    T.eq(w.files[INBOX], nil)
  end
end)

test("no reply in time: written, and the late reply's ack counts in the next exchange", function()
  local w, c, M, st = base({ core = { delay = 3 } })
  T.ok(M.cycle(st), "the message is in the mailbox: not a failure")
  T.eq(st.transport.answered, false)
  T.eq(st.failures, 0)
  T.ok(st.inv.machines[1].sensor, "no ack yet: the sensor lines wait")
  T.eq(#c.taken, 0, "the core hasn't taken it yet")
  T.run_until(M, st, w, 5)
  T.eq(#c.taken, 1, "taken after the exchange stopped waiting")
  T.ok(w.files[INBOX], "its reply waits in inbox.json")
  T.ok(st.inv.machines[1].sensor, "still no ack")
  -- The next exchange reads the late reply (ack 1) first, then sends message 2.
  c.delay = 0
  T.run_until(M, st, w, 9)
  T.eq(#c.taken, 2)
  T.eq(c.taken[2].seq, 2)
  T.eq(st.inv.machines[1].sensor, nil, "ack 1 dropped the lines message 1 carried")
  -- One reply an exchange: message 2's reply waits for the next one.
  T.eq(json.decode(w.files[INBOX]).ack, 2)
end)

test("the core isn't taking messages: the outbox is never overwritten; backoff, then recovery", function()
  local w, c, M, st = base({ core = { down = true } })
  T.ok(M.cycle(st), "message 1 is written")
  local first = w.files[OUTBOX]
  st.next_cycle = 0
  T.no(M.cycle(st), "message 2 can't go while message 1 waits")
  T.has(last_error(st).msg, "the core hasn't taken the last message")
  T.eq(last_error(st).where, "transport")
  T.eq(w.files[OUTBOX], first, "message 1 untouched")
  T.eq(st.failures, 1)
  T.eq(st.transport.n.full, 1)
  -- The core starts: message 1 is taken, then fresh ones flow; nothing written was lost.
  c.down = false
  T.run_until(M, st, w, 90)
  T.eq(st.failures, 0)
  T.eq(#c.taken, st.transport.n.written, "every message written was taken")
  T.eq(c.taken[1].seq, 1)
  for i = 2, #c.taken do
    T.ok(c.taken[i].seq > c.taken[i - 1].seq, "in order")
  end
  T.ok(c.taken[2].seq > 2, "seq counted the message that couldn't go (the core sees a gap, which is harmless)")
  local trans = 0
  for _, e in ipairs(c.taken[2].errors) do
    if e.where == "transport" then
      trans = trans + 1
    end
  end
  T.eq(trans, 1, "the waits reach the core as one transport error")
end)

test("a rename that returns false (the target held, on Windows) is tried again", function()
  local w, c, M, st = base()
  w.fail.rename = 3
  T.ok(M.cycle(st))
  T.eq(st.transport.n.rename_retries, 3)
  T.eq(#c.taken, 1)
  T.eq(w:calls_of("filesystem.rename"), 4)
end)

test("a rename that keeps failing fails the exchange; the next message goes over outbox.tmp", function()
  local w, c, M, st = base()
  w.fail.rename = 100
  T.no(M.cycle(st))
  T.has(last_error(st).msg, "can't rename outbox.tmp to outbox.json (8 tries)")
  T.eq(#c.taken, 0)
  w.fail.rename = 0
  st.next_cycle = 0
  T.ok(M.cycle(st))
  T.eq(#c.taken, 1)
  T.eq(c.taken[1].seq, 2, "the new message, never the stale one")
end)

test("a reply read twice (its delete failed) runs nothing twice", function()
  local w, c, M, st = base()
  c.queue = { T.cmd(41, "say", { text = "once only" }) }
  w.fail.remove = 1
  T.ok(M.cycle(st))
  T.eq(#w.chat.said, 1)
  T.ok(w.files[INBOX], "the reply is still there")
  T.eq(st.transport.n.remove_failed, 1)
  st.next_cycle = 0
  T.ok(M.cycle(st), "read again")
  T.eq(#w.chat.said, 1, "the say ran once")
  T.eq(w.files[INBOX], nil, "deleted the second time")
  T.has(w.files["/home/gos/gos-agent.log"], "can't delete inbox.json after reading it")
  T.run_until(M, st, w, 30)
  local seen = results_at_core(c)
  T.ok(seen[41], "its result reached the core")
end)

test("an ack from an earlier run is ignored; its commands still run, once", function()
  local w = T.base({ no_internet = true, extra = MAILBOX })
  w.filesystem.makeDirectory(DIR)
  w.files[INBOX] = json.encode({ v = 1, ok = true, boot = "deadbeef", ack = 40, cmds = json.array({ T.cmd(7, "say", { text = "from before" }) }) })
  local c = core(w)
  local M, st = T.agent(w)
  T.ok(M.cycle(st))
  T.eq(w.chat.said[1], "from before", "the old run's command ran")
  T.ok(st.inv.machines[1].sensor, "ack 40 of boot deadbeef dropped nothing of this run")
  T.run_until(M, st, w, 4)
  T.eq(c.taken[1].seq, 1)
  T.ok(c.taken[2], "and the next message went")
  T.eq(#w.chat.said, 1, "once")
  T.eq(#c.taken[2].results, 1, "with the say's result")
end)

test("a bad inbox: deleted, nothing run, the error reaches the core", function()
  local cases = {
    { "<html>", "bad reply in inbox.json" },
    { "[1,2]", "bad reply in inbox.json: not an object" },
    { '{"v":1,"ok":true,"cmds":[],"err":"' .. string.rep("x", 9000) .. '"}', "over reply_max (8192)" },
    { '{"v":2,"ok":true,"cmds":[]}', "contract v2" },
  }
  for _, case in ipairs(cases) do
    local w, c, M, st = base()
    c.raw = case[1]
    T.no(M.cycle(st), case[2])
    T.has(last_error(st).msg, case[2])
    T.eq(w.files[INBOX], nil, "deleted: " .. case[2])
    T.eq(#w.chat.said, 0)
    T.eq(#c.taken, 1, "the message itself went")
    T.run_until(M, st, w, 70) -- a reply of another contract version waits the longest backoff
    local found = false
    for i = 2, #c.taken do
      for _, e in ipairs(c.taken[i].errors) do
        found = found or (e.where == "transport" and e.msg:find(case[2], 1, true) ~= nil)
      end
    end
    T.ok(found, "reported to the core: " .. case[2])
  end
end)

test("the folder must be on a managed, writable drive", function()
  local cases = {
    { 'mailbox = { dir = "/tmp/gos" },', "a RAM disk" },
    { 'mailbox = { dir = "/mnt/ro/gos" },', "read-only" },
    { 'mailbox = { dir = "/dev/gos" },', "not on a drive" },
    { 'mailbox = { dir = "home/gos" },', "absolute folder" },
    { 'mailbox = { dir = "/home/../etc" },', "absolute folder" },
    { 'mailbox = { dir = "/" },', "absolute folder" },
    { 'mailbox = { dir = "/mnt/second" },', "not its top" },
    { 'mailbox = { wait_s = 9 },', "wait_s must be a number from 0 to 4" },
    { 'mailbox = { folder = "/home/gos" },', "unknown option folder" },
  }
  for _, case in ipairs(cases) do
    local w = T.base({ no_internet = true, extra = MAILBOX .. case[1] })
    w.mounts = {
      ["/"] = { address = w.boot_fs },
      ["/tmp"] = { address = w.tmp_address },
      ["/mnt/ro"] = { address = w.boot_fs, readonly = true },
      ["/dev"] = { address = "devfs" },
      ["/mnt/second"] = { address = w.boot_fs },
    }
    local M, st = T.agent(w, { no_transport = true })
    local t, err = M.open_transport(st)
    T.eq(t, nil, case[1])
    T.has(err, case[2], case[1])
  end
  local w = T.base({ no_internet = true, extra = MAILBOX .. 'mailbox = "yes",' })
  local M = assert(require("fakeoc").load_agent(w))
  local cfg, err = M.load_config("/home/gos/gos-agent.cfg")
  T.eq(cfg, nil)
  T.has(err, "mailbox must be false or a table")
end)

test("the start line says where the folder is, for gos.toml", function()
  local w = T.base({ no_internet = true, extra = MAILBOX .. 'mailbox = { dir = "/mnt/2nd/gos" },' })
  local second = "2d2d2d2d-0000-4000-8000-000000000222"
  w.mounts = { ["/"] = { address = w.boot_fs }, ["/mnt/2nd"] = { address = second } }
  T.ok(w.component and true)
  w.devices[second] = { addr = second, type = "filesystem", methods = {} }
  local _, st = T.agent(w)
  T.eq(st.transport.drive, second)
  T.eq(st.transport.rel, "gos")
  T.has(w.files["/home/gos/gos-agent.log"], ('drive = "%s", dir = "gos"'):format(second))
  T.ok(w.dirs["/mnt/2nd/gos"], "the folder is made")
  -- The default: mailbox/ next to the config, on the boot drive.
  local w2 = T.base({ no_internet = true, extra = MAILBOX })
  T.agent(w2)
  T.has(w2.files["/home/gos/gos-agent.log"], ('drive = "%s", dir = "home/gos/mailbox"'):format(w2.boot_fs))
end)

-- OC's Capacity counts every byte OC writes into a new file and never learns of a file deleted
-- or replaced from outside (F§2.3). The core renames outbox.json back to outbox.tmp, which the
-- next message overwrites (OC subtracts the old size first), so the count stays flat.
local FREE = 512 * 1024 -- what OpenOS leaves free on a small hard drive, about

local function space_hour(rename_to)
  local w, c, M, st = base({ extra = "sensor_s = 15," })
  c.rename_to = rename_to
  w.capacity = w.used + FREE
  T.run_until(M, st, w, 120)
  local after_warmup = w.used
  T.run_until(M, st, w, 3600)
  return w, c, st, after_warmup
end

local function out_of_space(st)
  for _, e in ipairs(st.errors) do
    if e.msg:find("not enough space", 1, true) then
      return true
    end
  end
  return false
end

test("an hour: OC's space count for the drive stays flat", function()
  local w, c, st, start = space_hour(nil)
  T.ok(#c.taken > 650, "about 720 messages in the hour, got " .. #c.taken)
  T.ok(w.used <= start + 70 * 1024, ("count %d after the first two minutes, %d after an hour"):format(start, w.used))
  T.no(out_of_space(st), "never out of space")
  T.eq(st.failures, 0)
end)

test("the contrast: a core taking outbox.json to another name fills OC's count within the hour", function()
  local w, c, st = space_hour(DIR .. "/outbox.read")
  T.ok(out_of_space(st), ("the agent hit 'not enough space' (count %d of %d, %d messages)"):format(w.used, w.capacity, #c.taken))
  T.ok(#c.taken < 600, "and stopped sending: " .. #c.taken)
end)

-- An hour against a jittery core: delays past wait_s, renames and removes failing, a two-minute
-- outage, a reply left unread past its TTL (a paused game), commands every two minutes, and an
-- agent restart (a new boot) with a reply of the old run waiting.
test("an hour, jittery core: no lost or half-read files, no command run twice", function()
  local w, c, M, st = base({ extra = "sensor_s = 15,", core = { ttl_s = 12 } })
  local rng = 4242
  local function rand(n)
    rng = (rng * 1103515245 + 12345) % 2147483648
    return math.floor(rng / 65536) % n -- the high bits: an LCG's low bits repeat quickly
  end
  local next_id, sent_ids = 100, {}
  local function chaos(world)
    if rand(10) == 0 then
      world.fail.rename = 1 + rand(3)
    end
    if rand(15) == 0 then
      world.fail.remove = 1
    end
    if not c.pinned then
      c.delay = rand(8) == 0 and 2.5 or rand(4) * 0.1
    end
    c.down = world.clock > 1500 and world.clock < 1620
    if math.floor(world.clock) % 120 < 5 and #c.queue == 0 then
      next_id = next_id + 1
      local kind = ({ "say", "machine_pause", "machine_resume" })[1 + next_id % 3]
      local args = kind == "say" and { text = "tick " .. next_id } or { addr = world.ebf.addr }
      c.queue[#c.queue + 1] = T.cmd(next_id, kind, args)
      sent_ids[#sent_ids + 1] = next_id
    end
    world:at(world.clock + 5, chaos)
  end
  w:at(w.clock + 5, chaos)
  T.run_until(M, st, w, 2000)
  -- A paused game: the agent writes a message and stops while it waits for the reply; the
  -- core answers late (with a command) and nothing reads it for 30 s. The reply is withdrawn and
  -- the command handed back.
  c.pinned, c.delay = true, 6
  T.run_until(M, st, w, math.max(w.clock, st.next_cycle) + 1.5)
  T.ok(w.files[OUTBOX], "the message is written")
  T.eq(w.files[INBOX], nil, "no reply yet")
  c.queue[#c.queue + 1] = T.cmd(999, "say", { text = "withdrawn before it is read" })
  w:advance_to(w.clock + 30)
  c.pinned = false
  local withdrawn = {}
  for _, id in ipairs(c.withdrawn) do
    withdrawn[id] = true
  end
  T.ok(withdrawn[999], "the unread reply was withdrawn and its command handed back")
  -- Restart the agent (a new boot, seq from 1) once nothing it ran waits for an ack.
  local t = 2460
  repeat
    T.run_until(M, st, w, t)
    t = t + 10
  until #st.results == 0 or t > 2700
  T.eq(#st.results, 0)
  local written = st.transport.n.written
  local M2, st2 = T.agent(w, { boot = "c0ffee00" })
  T.run_until(M2, st2, w, 3600)
  -- Every message written was taken once, whole, in order within its boot.
  local waiting_out = w.files[OUTBOX] and 1 or 0
  T.eq(#c.taken + waiting_out, written + st2.transport.n.written, "every written message taken")
  T.eq(c.repeats, 0)
  local last = {}
  for _, doc in ipairs(c.taken) do
    T.eq(doc.v, 1)
    if last[doc.boot] then
      T.ok(doc.seq > last[doc.boot], "in order within a boot")
    end
    last[doc.boot] = doc.seq
  end
  T.ok(last.b0075eed and last.c0ffee00, "both boots seen")
  -- Every reply the core wrote was read, withdrawn, or is still waiting.
  local waiting_in = w.files[INBOX] and 1 or 0
  T.eq(c.consumed + c.withdrawn_replies + waiting_in, #c.replies, "every reply read, withdrawn or waiting")
  T.ok(c.withdrawn_replies >= 1)
  -- Every command ran once and its result reached the core, or it was withdrawn unread.
  for _, id in ipairs(c.withdrawn) do
    withdrawn[id] = true
  end
  local at_core = results_at_core(c)
  local ran = 0
  for _, id in ipairs(sent_ids) do
    T.ok((at_core[id] ~= nil) ~= (withdrawn[id] == true), "command " .. id .. ": result at the core xor withdrawn")
    ran = ran + (at_core[id] and 1 or 0)
  end
  T.eq(at_core[999], nil, "the withdrawn command never ran")
  local said = {}
  for _, text in ipairs(w.chat.said) do
    T.eq(said[text], nil, "said once: " .. text)
    said[text] = true
  end
  T.ok(#sent_ids >= 25 and ran >= 20, ("commands: %d sent, %d ran"):format(#sent_ids, ran))
  T.ok(st.transport.n.rename_retries + st2.transport.n.rename_retries > 0, "renames were retried")
  T.ok(st.transport.n.remove_failed + st2.transport.n.remove_failed > 0, "replies were read twice")
  T.ok(st.transport.n.full + st2.transport.n.full > 0, "the outage made the agent wait")
  local log = (w.files["/home/gos/gos-agent.log.1"] or "") .. (w.files["/home/gos/gos-agent.log"] or "")
  T.has(log, "; mailbox: ", "the heartbeat carries the mailbox's counts")
  T.ok(#st2.results <= 16 and #st2.errors <= 8, "bounded")
end)

test("--once over the mailbox: answered, or written with no reply yet", function()
  local w = T.base({ no_internet = true, extra = MAILBOX })
  core(w)
  local M = assert(require("fakeoc").load_agent(w))
  local lines = {}
  local code = M.main(table.pack("--once", "/home/gos/gos-agent.cfg"), { out = function(s)
    lines[#lines + 1] = s
  end })
  T.eq(code, 0)
  T.has(table.concat(lines, "\n"), "the core answered")
  -- The stop line carries the mailbox's counts (the Lab soak compares them with the core's).
  T.has(table.concat(lines, "\n"), "stopped; 1 cycles, 1 sent, 0 failed; mailbox: 1 written, 1 replies, 0 waits for the core")
  local w2 = T.base({ no_internet = true, extra = MAILBOX })
  core(w2, { down = true })
  local M2 = assert(require("fakeoc").load_agent(w2))
  local lines2 = {}
  local code2 = M2.main(table.pack("--once", "/home/gos/gos-agent.cfg"), { out = function(s)
    lines2[#lines2 + 1] = s
  end })
  T.eq(code2, 1)
  T.has(table.concat(lines2, "\n"), "no reply from the core yet")
  T.ok(w2.files[OUTBOX], "the message waits in the mailbox")
end)

-- The golden M1 reply: an ack with its boot, and a command (tests/fixtures/oc/reply-ack.json).
test("golden mailbox reply", function()
  local w, c, M, st = base()
  T.ok(M.cycle(st))
  c.queue = { T.cmd(104, "say", { text = "Mailbox up" }, "voice") }
  st.next_cycle = 0
  T.ok(M.cycle(st))
  T.dump("golden-reply-ack.json", c.replies[2])
  local reply = json.decode(c.replies[2])
  T.eq(reply.boot, "b0075eed")
  T.eq(reply.ack, 2)
  T.eq(w.chat.said[1], "Mailbox up")
end)

return tests
