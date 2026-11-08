--[[ mailbox.lua: gos-agent's M1 transport, the file mailbox (WO-7.3; plan 7.9 "M1 mailbox
protocol", research F§2; contract gos-oc-bridge/1 in docs/contracts/oc_bridge.md; D216).
Written for this repo (MIT, see LICENSE at the repo root).

From MV, before an internet card, the agent and the core exchange the contract's two documents
through two files in one folder of this computer's managed drive. The core sees the drive as the
folder <world>/opencomputers/<drive address>/ of the save, and GTNH sets bufferChanges=false, so
each side sees the other's change on its next call.

  outbox.json  agent -> core. Write outbox.tmp, close it, rename it to outbox.json, and only while
               there is no outbox.json: the core renames it back to outbox.tmp once it has read
               it, and this side waits for that, so a message is never overwritten unread. (The
               next message then goes over the old outbox.tmp, which keeps OC's space count for
               the drive flat; see D216.) A rename that returns false is tried again.
  inbox.json   core -> agent. Polled with exists (a direct call; list and rename cost a tick
               each), read in 2 KB chunks (the most OC gives per read), then deleted: that is the
               acknowledgement the core waits for before it writes the next reply.

Every message carries the agent's boot and seq, and a reply the boot and highest seq the core has
(ack), so a missed or repeated read on either side is harmless; the agent never runs a command id
twice. An exchange costs one rename and, when a reply came, one remove (a server tick each);
everything else is direct.

  local t, err = mailbox.new(cfg, deps)  -- deps: json, log(level, msg), dir (the config's folder), sleep
  t.name           "mailbox"
  t.implicit_ack   false: inbox.json says what the core has read
  t:exchange(body, seq) -> reply | nil, err
  t.answered       false when the last exchange got no reply in time (the message was written)
  t:stats()        a line for the agent's heartbeat
  t:close()

Options, gos-agent.cfg's mailbox table:
  dir     the folder: absolute, on a managed, writable drive, never /tmp (a RAM disk) or a
          read-only disk (default: mailbox/ next to gos-agent.cfg, e.g. /home/gos/mailbox)
  wait_s  how long an exchange waits for the core to take the last message, and then for the
          reply to this one (default 2; 0 to 4)

Keep this file valid Lua 5.2, like gos-agent.lua (the tests compile it with luac5.2).
]]

local component = require("component")
local computer = require("computer")
local filesystem = require("filesystem")

local M = {
  POLL_S = 0.25, -- between exists checks while waiting
  RENAME_TRIES = 8, -- a rename returns false while another program holds the target (Windows)
  WAIT_MAX = 4,
}

M.DEFAULTS = { dir = false, wait_s = 2 }

local PART = "^[%w_][%w_%.%-]*$"

local function now()
  return computer.uptime()
end

-- The options, checked, with dir made absolute and clean. default_dir: the agent's folder's
-- mailbox/. Returns the options or nil and why.
function M.check(opts, default_dir)
  if opts == nil or opts == false then
    opts = {}
  end
  if type(opts) ~= "table" then
    return nil, "mailbox must be false or a table of options"
  end
  local out = { dir = default_dir, wait_s = M.DEFAULTS.wait_s }
  for k, v in pairs(opts) do
    if M.DEFAULTS[k] == nil then
      return nil, "mailbox: unknown option " .. tostring(k) .. " (it takes dir and wait_s)"
    end
    out[k] = v
  end
  local w = out.wait_s
  if type(w) ~= "number" or w ~= w or w < 0 or w > M.WAIT_MAX then
    return nil, ("mailbox: wait_s must be a number from 0 to %d"):format(M.WAIT_MAX)
  end
  local dir = out.dir
  local bad = "mailbox: dir must be an absolute folder like /home/gos/mailbox (letters, digits, _ . -)"
  if type(dir) ~= "string" or dir:sub(1, 1) ~= "/" or #dir > 128 then
    return nil, bad
  end
  local parts = {}
  for part in dir:gmatch("[^/]+") do
    if not part:find(PART) then
      return nil, bad
    end
    parts[#parts + 1] = part
  end
  if #parts == 0 then
    return nil, bad
  end
  out.dir = "/" .. table.concat(parts, "/")
  return out
end

local Box = {}
Box.__index = Box

function M.new(cfg, deps)
  deps = deps or {}
  local opts, err = M.check(cfg.mailbox, (deps.dir or "/home/gos") .. "/mailbox")
  if not opts then
    return nil, err
  end
  local dir = opts.dir
  -- Only a managed drive is a plain folder the core can see: not the RAM disk, not a read-only
  -- disk, not a virtual file system (an unmanaged drive can't be mounted at all).
  local fs, mount = filesystem.get(dir)
  if not fs then
    return nil, "mailbox: no drive holds " .. dir .. " (" .. tostring(mount) .. ")"
  end
  if computer.tmpAddress and fs.address == computer.tmpAddress() then
    return nil, "mailbox: " .. dir .. " is on /tmp, a RAM disk the core can't see; use a folder on a managed hard drive"
  end
  if component.type(fs.address) ~= "filesystem" then
    return nil, "mailbox: " .. dir .. " is not on a drive; use a folder on a managed hard drive"
  end
  if fs.isReadOnly() then
    return nil, "mailbox: the drive holding " .. dir .. " is read-only"
  end
  local rel
  if mount == "/" then
    rel = dir:sub(2)
  else
    rel = dir:sub(#mount + 2)
  end
  if rel == "" then
    return nil, "mailbox: use a folder inside the drive, not its top (e.g. " .. mount .. "/gos-mailbox)"
  end
  if not filesystem.isDirectory(dir) then
    local ok, merr = filesystem.makeDirectory(dir)
    if not ok then
      return nil, "mailbox: can't make " .. dir .. ": " .. tostring(merr)
    end
  end
  local t = setmetatable({
    name = "mailbox",
    implicit_ack = false,
    answered = nil,
    json = deps.json,
    log = deps.log or function() end,
    sleep = deps.sleep or os.sleep,
    dir = dir,
    drive = fs.address,
    rel = rel,
    wait_s = opts.wait_s,
    reply_max = cfg.reply_max or 8192,
    outbox = dir .. "/outbox.json",
    tmp = dir .. "/outbox.tmp",
    inbox = dir .. "/inbox.json",
    n = { written = 0, replies = 0, full = 0, rename_retries = 0, remove_failed = 0 },
  }, Box)
  -- What gos.toml needs to find this folder ([mailbox.lab] or [mailbox.play]).
  t.log("info", ('mailbox %s on drive %s; in gos.toml: drive = "%s", dir = "%s"'):format(dir, fs.address, fs.address, rel))
  return t
end

-- Write one message: wait (up to wait_s) for the core to take the last one, write outbox.tmp,
-- close it, rename it to outbox.json. Returns true, or nil and why.
function Box:send(body)
  local deadline = now() + self.wait_s
  while filesystem.exists(self.outbox) do
    if now() >= deadline then
      self.n.full = self.n.full + 1
      return nil, ("the core hasn't taken the last message (outbox.json is still there after %s s)"):format(self.wait_s)
    end
    self.sleep(M.POLL_S)
  end
  local h, err = filesystem.open(self.tmp, "w")
  if not h then
    return nil, "can't write outbox.tmp: " .. tostring(err)
  end
  local ok, werr = h:write(body)
  h:close()
  if not ok then
    return nil, "can't write outbox.tmp: " .. tostring(werr)
  end
  for i = 1, M.RENAME_TRIES do
    if filesystem.rename(self.tmp, self.outbox) then
      self.n.written = self.n.written + 1
      return true
    end
    if i < M.RENAME_TRIES then
      self.n.rename_retries = self.n.rename_retries + 1
      self.sleep(0.05)
    end
  end
  return nil, ("can't rename outbox.tmp to outbox.json (%d tries)"):format(M.RENAME_TRIES)
end

-- Read the core's reply if there is one (waiting up to `wait` s for it), then delete it.
-- Returns the reply, nothing (no reply), or nil and why.
function Box:receive(wait)
  local deadline = now() + (wait or 0)
  while not filesystem.exists(self.inbox) do
    if now() >= deadline then
      return nil
    end
    self.sleep(M.POLL_S)
  end
  local size = filesystem.size(self.inbox) or 0
  if size > self.reply_max then
    -- Never readable: delete it so the core can write the next one.
    filesystem.remove(self.inbox)
    return nil, ("inbox.json is %d bytes, over reply_max (%d); deleted unread"):format(size, self.reply_max)
  end
  local h = filesystem.open(self.inbox, "r")
  if not h then
    return nil -- gone since exists: the core withdrew it
  end
  local parts, total, over = {}, 0, false
  while true do
    local chunk = h:read(2048)
    if not chunk then
      break
    end
    total = total + #chunk
    if total > self.reply_max then
      over = true
      break
    end
    parts[#parts + 1] = chunk
  end
  h:close()
  if not filesystem.remove(self.inbox) then
    -- It stays and is read again next time: a repeated reply is harmless (D216).
    self.n.remove_failed = self.n.remove_failed + 1
    if self.n.remove_failed == 1 then
      self.log("warn", "can't delete inbox.json after reading it; the core waits until it is gone")
    end
  end
  if over then
    return nil, ("inbox.json is over reply_max (%d bytes); ignored"):format(self.reply_max)
  end
  self.n.replies = self.n.replies + 1
  local reply, derr = self.json.decode(table.concat(parts))
  if type(reply) ~= "table" or self.json.is_array(reply) then
    return nil, "bad reply in inbox.json: " .. tostring(derr or "not an object")
  end
  return reply
end

-- One exchange: a reply that arrived after the last exchange stopped waiting is taken first;
-- then this message goes out and, if none was waiting, its reply is awaited (wait_s). A reply
-- always reaches the agent, even when the message couldn't be written (it goes in the next one).
function Box:exchange(body, seq)
  self.answered = false
  local reply, rerr = self:receive(0)
  local sent, serr = self:send(body)
  if sent and reply == nil and rerr == nil then
    reply, rerr = self:receive(self.wait_s)
  end
  if reply then
    self.answered = true
    return reply
  end
  if not sent then
    return nil, serr
  end
  if rerr then
    return nil, rerr
  end
  -- Written; the core hasn't answered in time. Its reply comes with the next exchange.
  return { v = 1, ok = true, cmds = self.json.array() }
end

function Box:stats()
  local n = self.n
  return ("mailbox: %d written, %d replies, %d waits for the core, %d rename retries, %d removes failed")
    :format(n.written, n.replies, n.full, n.rename_retries, n.remove_failed)
end

function Box:close() end

return M
