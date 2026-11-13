--[[ display.lua: gos-agent's in-game displays (WO-7.4; plan 7.9 "In-game displays", D24, D76;
contract gos-oc-bridge/1, "The display", in docs/contracts/oc_bridge.md; D277-D279). Written for
this repo (MIT, see LICENSE at the repo root).

Displays are not actions and need no confirmation (plan 7.9). The core composes every line; this
module only lays them out, so the same reply's `display` works over M1 (the mailbox) and M2 (HTTP):

  board  the OC screen status board: power, problems, the action waiting for "confirm", the goal,
         the next step, AE2 crafting. The GPU is set to at most 50 x 16 (OC's smallest screen and
         GPU, so larger screens show bigger text): a header (GOS and how fresh the core's reply
         is), the core's lines (at most 12, each cut to the width), and a footer with the agent's
         own last note. Only rows that changed are written. While no reply has come for STALE_S,
         the header says so and every line turns grey: old content is marked, never presented as
         current (plan 7.9).
  hud    OCGlasses text (M2 only: an HV/EV base with an internet card, plan 7.9's mode table). A
         fixed set of HUD_MAX text widgets is made once; a new display sets only the lines that
         changed, all in one go (each add or setter is a packet to every bound player, and the
         terminal draws widgets/10 OC energy a tick [F 4.1]).
  chat   chat-box notices the core sends (confirmations of world actions): each said once, in id
         order, at most 2 a reply (say costs a server tick), with control characters and colour
         codes removed. Free text in chat stays the `say` command, which Clayton confirms.

Every text from the core is data: drawn or said, never run (no load of anything received).

  local d, err = display.new(opts, deps)  -- opts: gos-agent.cfg's display (true, false or a table)
       deps: call(addr, method, ...) -> ok, ...   (the agent's component call), log(level, msg), now()
  d:attach(inv, transport_name)  after each discovery (inv.gpu, inv.screens, inv.glasses, inv.chat)
  d:state()                      the telemetry's `display`: { rev, said, board = {w, h}?, hud, chat }
  d:apply(doc)                   a reply's `display`
  d:tick(ok_at)                  every cycle: ok_at = uptime of the last reply from the core
  d:note(msg)                    the agent's own last note, in the footer
  d:close()                      blank the board, remove the HUD widgets

Options (gos-agent.cfg's display table; true is all defaults, false turns every display off):
  board = true    draw the status board on a screen
  screen = false  address prefix of the screen to use (default: the primary screen)
  hud = true      draw OCGlasses text when a glasses terminal is in and the transport is http
  chat = true     say the core's chat notices on the chat box

Keep this file valid Lua 5.2, like gos-agent.lua (the tests compile it with luac5.2).
]]

local D = {
  W = 50, -- OC's tier-1 screen and GPU: everything fits the smallest
  H = 16,
  LINES = 12, -- the core sends at most 12
  HUD_MAX = 4,
  HUD_W = 40,
  CHAT_MAX = 2,
  CHAT_W = 160,
  STALE_S = 30, -- the core's own stale limit (D219)
}

D.DEFAULTS = { board = true, screen = false, hud = true, chat = true }

D.COLORS = {
  ok = 0x55FF55,
  warn = 0xFFFF55,
  bad = 0xFF5555,
  info = 0xFFFFFF,
  dim = 0x888888,
}
local BLACK = 0x000000

-- HUD colours as 0..1 floats (OCGlasses' setColor takes r, g, b in 0..1).
local function rgb(c)
  return math.floor(c / 65536) % 256 / 255, math.floor(c / 256) % 256 / 255, c % 256 / 255
end

function D.check(opts)
  if opts == false then
    return { board = false, screen = false, hud = false, chat = false }
  end
  if opts == nil or opts == true then
    opts = {}
  end
  if type(opts) ~= "table" then
    return nil, "display must be true, false or a table of display options"
  end
  local out = {}
  for k, v in pairs(D.DEFAULTS) do
    out[k] = v
  end
  for k, v in pairs(opts) do
    if D.DEFAULTS[k] == nil then
      return nil, "display: unknown option " .. tostring(k) .. " (it takes board, screen, hud and chat)"
    end
    out[k] = v
  end
  for _, k in ipairs({ "board", "hud", "chat" }) do
    if type(out[k]) ~= "boolean" then
      return nil, "display: " .. k .. " must be true or false"
    end
  end
  if out.screen ~= false and (type(out.screen) ~= "string" or #out.screen < 3) then
    return nil, "display: screen must be false or a screen address prefix of at least 3 characters"
  end
  return out
end

-- Printable ASCII only, cut to n: the core sends nothing else, and a line is drawn, never run.
local function printable(s, n)
  if type(s) ~= "string" then
    return ""
  end
  s = s:gsub("[^\32-\126]", "?")
  if #s > n then
    s = s:sub(1, n)
  end
  return s
end

local LEVEL = { ok = true, warn = true, bad = true, info = true, dim = true }

local function lines_of(list, max, width)
  local out = {}
  if type(list) ~= "table" then
    return out
  end
  for i = 1, math.min(#list, max) do
    local ln = list[i]
    if type(ln) == "table" then
      out[#out + 1] = { t = printable(ln.t, width), c = LEVEL[ln.c] and ln.c or "info" }
    end
  end
  return out
end

local Display = {}
Display.__index = Display

function D.new(opts, deps)
  local cfg, err = D.check(opts)
  if not cfg then
    return nil, err
  end
  return setmetatable({
    cfg = cfg,
    call = deps.call,
    log = deps.log or function() end,
    now = deps.now,
    rev = 0, -- what is shown
    said = 0, -- the highest notice id said this run
    lines = {}, -- the core's board lines
    hud = {},
    drawn = {}, -- row -> "colour|text" as it is on the screen
    widgets = {}, -- HUD text widgets
    widget_text = {},
    stale = false,
    ok_at = nil,
    footer = "",
  }, Display)
end

---------------------------------------------------------------------------------------------
-- Setup

local function pick_screen(screens, prefix)
  if not screens or #screens == 0 then
    return nil
  end
  if prefix then
    for _, a in ipairs(screens) do
      if a:sub(1, #prefix) == prefix then
        return a
      end
    end
    return nil
  end
  return screens[1]
end

function Display:attach(inv, transport)
  self.transport = transport
  local gpu, screen = inv.gpu, pick_screen(inv.screens, self.cfg.screen or nil)
  if self.cfg.board and gpu and screen then
    if gpu ~= self.gpu or screen ~= self.screen then
      self.gpu, self.screen, self.w, self.h = nil, nil, nil, nil
      local ok, err = self.call(gpu, "bind", screen, false)
      local okr, mw, mh = false, nil, nil
      if ok then
        okr, mw, mh = self.call(gpu, "maxResolution")
      end
      if ok and okr and type(mw) == "number" and type(mh) == "number" then
        local w, h = math.min(mw, D.W), math.min(mh, D.H)
        if w >= 10 and h >= 4 and self.call(gpu, "setResolution", w, h) then
          self.gpu, self.screen, self.w, self.h = gpu, screen, math.floor(w), math.floor(h)
          self.drawn = {}
          self:draw(true)
        end
      end
      if not self.gpu then
        self.log("warn", "display: no status board (the GPU or screen didn't answer: " .. tostring(err or mw) .. ")")
      end
    end
  elseif self.gpu then
    self.gpu, self.screen = nil, nil
  end
  local glasses = self.cfg.hud and transport == "http" and inv.glasses or nil
  if glasses ~= self.glasses then
    self:clear_hud()
    self.glasses = glasses
    self.widget_text = {}
  end
  self.chat = self.cfg.chat and inv.chat or nil
end

function Display:board_on()
  return self.gpu ~= nil
end

function Display:state()
  local s = { rev = self.rev, said = self.said, hud = self.glasses ~= nil, chat = self.chat ~= nil }
  if self.gpu then
    s.board = { w = self.w, h = self.h }
  end
  return s
end

---------------------------------------------------------------------------------------------
-- The board

local function row_text(text, w)
  if #text > w then
    return text:sub(1, w)
  end
  return text .. (" "):rep(w - #text)
end

-- Write one row if it changed: colour then text, one set call.
function Display:put(y, text, colour)
  local key = colour .. "|" .. text
  if self.drawn[y] == key then
    return
  end
  local gpu = self.gpu
  self.call(gpu, "setForeground", D.COLORS[colour] or D.COLORS.info)
  self.call(gpu, "set", 1, y, row_text(text, self.w))
  self.drawn[y] = key
end

function Display:header()
  local right
  if not self.ok_at then
    right = "waiting for the core"
  else
    local age = math.max(0, math.floor(self.now() - self.ok_at))
    if self.stale then
      right = ("NO REPLY %ds"):format(age)
    else
      right = ("%s %ds"):format(self.transport == "http" and "M2" or "M1", age)
    end
  end
  local left = "GREGTEC OS"
  local gap = math.max(1, self.w - #left - #right)
  return (left .. (" "):rep(gap) .. right):sub(1, self.w), self.stale and "bad" or "info"
end

function Display:draw(full)
  if not self.gpu then
    return
  end
  if full then
    self.call(self.gpu, "setBackground", BLACK)
    self.call(self.gpu, "fill", 1, 1, self.w, self.h, " ")
    self.drawn = {}
  end
  local text, colour = self:header()
  self:put(1, text, colour)
  local body = self.h - 2
  for i = 1, body do
    local ln = self.lines[i]
    if ln then
      self:put(i + 1, ln.t, self.stale and "dim" or ln.c)
    elseif i == 1 and self.rev == 0 then
      self:put(2, "Waiting for the first status from Gregtec OS...", "dim")
    else
      self:put(i + 1, "", "info")
    end
  end
  self:put(self.h, self.footer, "dim")
end

function Display:note(msg)
  local text = printable(msg, self.w or D.W)
  if text ~= self.footer then
    self.footer = text
    if self.gpu then
      self:put(self.h, self.footer, "dim")
    end
  end
end

---------------------------------------------------------------------------------------------
-- The HUD

function Display:clear_hud()
  if not self.glasses then
    self.widgets = {}
    return
  end
  for _, wdg in ipairs(self.widgets) do
    local okid, id = pcall(wdg.getID)
    if okid and id ~= nil then
      self.call(self.glasses, "removeObject", id)
    end
  end
  self.widgets = {}
end

function Display:draw_hud()
  if not self.glasses then
    return
  end
  if #self.widgets == 0 then
    for i = 1, D.HUD_MAX do
      local ok, wdg = self.call(self.glasses, "addTextLabel")
      if not ok or type(wdg) ~= "table" then
        self.log("warn", "display: no HUD (the glasses terminal didn't add a text widget)")
        self:clear_hud()
        self.glasses = nil
        return
      end
      pcall(wdg.setPosition, 4, 4 + (i - 1) * 10)
      pcall(wdg.setScale, 1)
      self.widgets[i] = wdg
    end
  end
  for i = 1, D.HUD_MAX do
    local ln = self.hud[i]
    local text, colour = "", "info"
    if ln then
      text, colour = ln.t, self.stale and "dim" or ln.c
    end
    local key = colour .. "|" .. text
    if self.widget_text[i] ~= key then
      local wdg = self.widgets[i]
      pcall(wdg.setText, text)
      pcall(wdg.setColor, rgb(D.COLORS[colour]))
      self.widget_text[i] = key
    end
  end
end

---------------------------------------------------------------------------------------------
-- From the core

function Display:apply(doc)
  if type(doc) ~= "table" then
    return
  end
  local rev = doc.rev
  if math.type and math.type(rev) == "integer" and rev >= 1 and rev ~= self.rev then
    self.rev = rev
    self.lines = lines_of(doc.lines, D.LINES, D.W)
    self.hud = lines_of(doc.hud, D.HUD_MAX, D.HUD_W)
    self:draw(false)
    self:draw_hud()
  end
  if self.chat and type(doc.chat) == "table" then
    local n = 0
    for _, c in ipairs(doc.chat) do
      if n >= D.CHAT_MAX then
        break
      end
      if type(c) == "table" and math.type(c.id) == "integer" and c.id > self.said then
        local text = printable(c.t, D.CHAT_W)
        if text ~= "" then
          n = n + 1
          local ok, res = self.call(self.chat, "say", text)
          if not ok or res == false then
            self.log("warn", "display: the chat box didn't say a notice: " .. tostring(res))
          end
        end
        self.said = c.id -- said or not, never tried twice
      end
    end
  end
end

function Display:tick(ok_at)
  self.ok_at = ok_at
  local stale = ok_at ~= nil and self.now() - ok_at > D.STALE_S
  if stale ~= self.stale then
    self.stale = stale
    self:draw(false)
    self:draw_hud()
  elseif self.gpu then
    local text, colour = self:header()
    self:put(1, text, colour)
  end
end

function Display:close()
  if self.gpu then
    self.call(self.gpu, "setBackground", BLACK)
    self.call(self.gpu, "setForeground", D.COLORS.info)
    self.call(self.gpu, "fill", 1, 1, self.w, self.h, " ")
  end
  self:clear_hud()
end

return D
