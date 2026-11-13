--[[ fakeoc.lua: a fake OpenComputers world for testing oc/*.lua on plain Lua 5.3 (WO-7.2).

It mimics what the agent touches, as OC 1.12.68-GTNH, OpenOS and GTNH 2.9's drivers behave (read
from their sources; nothing copied):

  * component: list (a table that is also its own iterator), type, methods, invoke, proxy,
    isAvailable, getPrimary and component.<type>. A missing block raises "no such component", a
    missing method "no such method" and a bad argument its message, as OC does (dev.raise). A
    method whose Java side threw anything else doesn't raise: OC's architecture hands the call
    nil plus the exception's message, or "unknown error" when it has none (dev.fail).
  * Time: a virtual clock behind computer.uptime(). A non-direct component call costs one server
    tick (0.05 s), a direct call none. event.pull and os.sleep move the clock. Device changes
    happen at scheduled times (setWorkAllowed on the next tick, an AE2 job finishing, an HTTP
    reply arriving).
  * event: OpenOS semantics. pull/pullFiltered, listen/ignore (listeners run on every pull,
    os.sleep's too), push. Ctrl+C is an "interrupted" signal; Ctrl+Alt+C a hard "interrupted"
    error from the pull.
  * filesystem: OpenOS's library over an in-memory drive; handles read at most 2048 bytes a call.
    rename and remove are non-direct (a tick each). The drive counts its space as OC's Capacity
    does: a new file adds fileCost (512) and every byte OC writes, a file OC truncates or deletes
    is subtracted, and files changed from outside (tests writing w.files, the fake core) are never
    counted (w.used; w.capacity makes writes fail with "not enough space"). get() finds a path's
    drive in w.mounts (the boot drive at /, /tmp the RAM disk).
  * internet: OpenOS's library over a fake internet card: request() returns a handle whose
    finishConnect/read are non-direct and response/close direct, read returns "" until data is
    there and nil at the end, at most 4 connections may be open, and failures return nil plus the
    IOException text, as OC's architecture does for a callback exception with a message.
  * A yield watchdog: 5 million Lua instructions without the clock moving raise "too long without
    yielding", as OC kills a computer that never yields.

Devices: gt_machine (GT multiblock / LSC via Computronics' merged drivers), gt_batterybuffer,
me_controller, me_interface (block, or part without network methods), chat_box, tps_card, internet
and plain ones (screen, gpu, keyboard, filesystem, eeprom).
]]

local F = {}

local TICK = 0.05
local YIELD_LIMIT = 50 -- hook calls (100k instructions each) without the clock moving

local World = {}
World.__index = World

local seq_addr = 0
-- A UUID-shaped address, as OC gives components.
function F.addr(prefix)
  seq_addr = seq_addr + 1
  local p = (prefix or "") .. string.format("%08x", seq_addr)
  p = p:sub(1, 8)
  return string.format("%s-0000-4000-8000-%012x", p, seq_addr)
end

function F.reset_addresses()
  seq_addr = 0
end

---------------------------------------------------------------------------------------------
-- World, clock, timers, signals

function F.world(opts)
  opts = opts or {}
  local w = setmetatable({
    clock = opts.clock or 1.0,
    devices = {},
    calls = {}, -- "type.method" -> count
    nondirect = 0, -- non-direct calls (ticks spent)
    trace = {}, -- { t, type, method } of non-direct calls (and direct ones listed in w.watch)
    timers = {},
    queue = {},
    listeners = {},
    printed = {},
    files = {},
    mtimes = {},
    dirs = { ["/"] = true },
    mem_free = opts.mem_free or 900000,
    mem_total = opts.mem_total or 2097152,
    energy = 4800.0,
    energy_max = 5000.0,
    address = opts.address or "c0c0c0c0-0000-4000-8000-000000000001",
    arch = opts.arch or "Lua 5.3",
    requests = {},
    replies = {},
    server = nil,
    hard_interrupt_at = nil,
    fail = {}, -- one-shot failure injection: fs ops
    used = 0, -- the drive's space count, as OC's Capacity keeps it
    capacity = nil, -- bytes; nil: unlimited
    file_cost = 512, -- GTNH's fileCost
    tmp_address = "7f000000-0000-4000-8000-00000000ffff", -- computer.tmpAddress()
    mounts = nil, -- path -> { address, readonly }; nil: the boot drive at / and the tmpfs at /tmp
    fs_reads = {}, -- path -> read calls on handles of that path
    stuck = 0,
    seen_clock = nil,
  }, World)
  w.component = F.component_lib(w)
  w.computer = F.computer_lib(w)
  w.event = F.event_lib(w)
  w.filesystem = F.filesystem_lib(w)
  w.internet = F.internet_lib(w)
  w.os = setmetatable({
    sleep = function(s)
      w:sleep(s)
    end,
  }, { __index = os })
  return w
end

function World:advance_to(t)
  if t < self.clock then
    t = self.clock
  end
  while true do
    local best
    for i, tm in ipairs(self.timers) do
      if tm.t <= t and (not best or tm.t < self.timers[best].t) then
        best = i
      end
    end
    if not best then
      break
    end
    local tm = table.remove(self.timers, best)
    if tm.t > self.clock then
      self.clock = tm.t
    end
    tm.fn(self)
  end
  self.clock = t
  self.stuck = 0
end

function World:tick()
  self:advance_to(self.clock + TICK)
end

-- Run fn at virtual time t (or after `ticks` ticks).
function World:at(t, fn)
  self.timers[#self.timers + 1] = { t = t, fn = fn }
end

function World:after_ticks(n, fn)
  self:at(self.clock + n * TICK + 1e-9, fn)
end

function World:push(name, ...)
  self.queue[#self.queue + 1] = table.pack(name, ...)
  self.queue[#self.queue].t = self.clock
end

function World:signal_at(t, name, ...)
  local args = table.pack(name, ...)
  self:at(t, function(world)
    world.queue[#world.queue + 1] = args
    args.t = world.clock
  end)
end

-- computer.pullSignal: the next signal before the deadline, running timers on the way.
function World:pull_signal(timeout)
  local deadline = timeout and (self.clock + math.max(0, timeout)) or math.huge
  while true do
    if self.hard_interrupt_at and self.clock >= self.hard_interrupt_at then
      self.hard_interrupt_at = nil
      error("interrupted", 0)
    end
    if #self.queue > 0 then
      local s = table.remove(self.queue, 1)
      self.stuck = 0
      return table.unpack(s, 1, s.n)
    end
    local next_t
    for _, tm in ipairs(self.timers) do
      if not next_t or tm.t < next_t then
        next_t = tm.t
      end
    end
    if self.hard_interrupt_at and (not next_t or self.hard_interrupt_at < next_t) then
      next_t = self.hard_interrupt_at
    end
    if next_t and next_t <= deadline then
      self:advance_to(next_t)
    elseif deadline == math.huge then
      error("fake: pullSignal would wait forever", 0)
    else
      self:advance_to(deadline)
      return
    end
  end
end

-- OpenOS's dispatch: every pulled signal runs the listeners for its name.
function World:dispatch(sig)
  if not sig[1] then
    return
  end
  local list = self.listeners[sig[1]]
  if not list then
    return
  end
  local copy = {}
  for i, cb in ipairs(list) do
    copy[i] = cb
  end
  for _, cb in ipairs(copy) do
    local ok, res = pcall(cb, table.unpack(sig, 1, sig.n))
    if not ok then
      self.listener_errors = (self.listener_errors or 0) + 1
    elseif res == false then
      for i, c in ipairs(list) do
        if c == cb then
          table.remove(list, i)
          break
        end
      end
    end
  end
end

function World:pull(timeout)
  local sig = table.pack(self:pull_signal(timeout))
  self:dispatch(sig)
  return sig
end

-- OpenOS os.sleep: pulls (and so dispatches) until the deadline.
function World:sleep(s)
  local deadline = self.clock + (s or 0)
  repeat
    self:pull(deadline - self.clock)
  until self.clock >= deadline
end

function World:print(...)
  local parts = table.pack(...)
  for i = 1, parts.n do
    parts[i] = tostring(parts[i])
  end
  self.printed[#self.printed + 1] = table.concat(parts, "\t")
  if self.lean and #self.printed > 50 then
    table.remove(self.printed, 1)
  end
end

function World:printed_text()
  return table.concat(self.printed, "\n")
end

-- The yield watchdog (installed by the runner while the agent runs).
function World:instruction_hook()
  if self.seen_clock == self.clock then
    self.stuck = self.stuck + 1
    if self.stuck > YIELD_LIMIT then
      self.stuck = 0
      error("too long without yielding", 0)
    end
  else
    self.seen_clock = self.clock
    self.stuck = 0
  end
end

---------------------------------------------------------------------------------------------
-- Devices

function World:count(dtype, method, direct)
  local key = dtype .. "." .. method
  self.calls[key] = (self.calls[key] or 0) + 1
  if not direct then
    self.nondirect = self.nondirect + 1
    if not self.lean then
      self.trace[#self.trace + 1] = { t = self.clock, type = dtype, method = method }
    end
  end
end

function World:calls_of(key)
  return self.calls[key] or 0
end

-- Times (uptime) at which key was called (non-direct calls only).
function World:times_of(dtype, method)
  local out = {}
  for _, c in ipairs(self.trace) do
    if c.type == dtype and c.method == method then
      out[#out + 1] = c.t
    end
  end
  return out
end

function World:add(dev, quiet)
  self.devices[dev.addr] = dev
  dev.world = self
  if not quiet then
    self:push("component_added", dev.addr, dev.type)
  end
  return dev
end

function World:remove(addr)
  local dev = self.devices[addr]
  self.devices[addr] = nil
  if dev then
    self:push("component_removed", addr, dev.type)
  end
end

function World:invoke(addr, method, ...)
  local dev = self.devices[addr]
  if not dev then
    error("no such component", 0)
  end
  local m = dev.methods[method]
  if not m then
    error("no such method", 0)
  end
  self:count(dev.type, method, m.direct)
  if not m.direct then
    self:tick()
    if not self.devices[addr] then
      error("no such component", 0)
    end
  end
  -- dev.raise[method] / dev.fail[method]: a message (every call) or a list (one call each).
  local function injected(set)
    local msg = set and set[method]
    if type(msg) == "table" then
      msg = table.remove(msg, 1)
      if #set[method] == 0 then
        set[method] = nil
      end
    end
    return msg
  end
  local msg = injected(dev.raise)
  if msg then
    error(msg, 0) -- a Lua error: OC raises for bad arguments (IllegalArgumentException)
  end
  msg = injected(dev.fail)
  if msg then
    return nil, msg -- any other Java exception: nil, reason
  end
  return m.fn(dev, ...)
end

-- A userdata-like value (crafting status, craftable, HTTP request): methods called with a dot.
function World:value(vtype, methods)
  local v = {}
  for name, m in pairs(methods) do
    v[name] = function(...)
      self:count(vtype, name, m.direct)
      if not m.direct then
        self:tick()
      end
      return m.fn(...)
    end
  end
  return v
end

local function device(w, dtype, opts)
  return {
    addr = opts.addr or F.addr(opts.prefix),
    type = dtype,
    methods = {},
    raise = opts.raise or {},
    fail = opts.fail or {},
  }
end

local function direct(fn)
  return { direct = true, fn = fn }
end

local function nondirect(fn)
  return { direct = false, fn = fn }
end

-- A GT machine as GTNH 2.9 shows it: Computronics' DriverMachine (priority 1, so the name
-- gt_machine), DriverBaseMetaTileEntity and DriverDeviceInformation, merged with OC's
-- gt_energyContainer (and DriverLSC for an LSC).
function F.gt_machine(w, opts)
  opts = opts or {}
  local d = device(w, opts.type or "gt_machine", opts)
  d.name = opts.name or "multimachine.blastfurnace"
  d.xyz = opts.xyz or { 100, 64, -20 }
  d.active = opts.active ~= false
  d.allowed = opts.allowed ~= false
  d.work = opts.work ~= false
  d.progress = opts.progress or 120
  d.progress_max = opts.progress_max or 400
  d.eu = opts.eu or 0
  d.eu_max = opts.eu_max or 0
  d.eu_in = opts.eu_in or 0
  d.eu_out = opts.eu_out or 0
  d.sensor = opts.sensor or {
    "Owned by: \194\1676Clayton",
    "Meta-ID: \194\1676" .. "1000 \194\167avalid",
    "Progress: \194\167a6 \194\167rs / \194\167e20 \194\167rs",
    "Stored Energy: \194\167a12,000 \194\167rEU / \194\167e16,000 \194\167rEU",
    "Problems: \194\167c" .. tostring(opts.problems or 0),
    "GT5U.multiblock.scanner.efficiency\\\\\194\167e100\194\167r",
    "Total runtime: \194\1676" .. "3\194\167r hours",
  }
  d.stored_str = opts.stored_str
  d.capacity_str = opts.capacity_str
  local m = d.methods
  -- Computronics, direct
  m.getEUStored = direct(function(s) return s.eu end)
  m.getEUMaxStored = direct(function(s) return s.eu_max end)
  m.getEUInputAverage = direct(function(s) return s.eu_in end)
  m.getEUOutputAverage = direct(function(s) return s.eu_out end)
  m.getSteamStored = direct(function() return 0 end)
  m.getSteamMaxStored = direct(function() return 0 end)
  m.getOwnerName = direct(function() return "Clayton" end)
  m.hasWork = direct(function(s) return s.work end)
  m.getWorkProgress = direct(function(s) return s.progress end)
  m.getWorkMaxProgress = direct(function(s) return s.progress_max end)
  m.isWorkAllowed = direct(function(s) return s.allowed end)
  m.isMachineActive = direct(function(s) return s.active end)
  m.getCoordinates = direct(function(s) return s.xyz[1], s.xyz[2], s.xyz[3] end)
  m.getName = direct(function(s) return s.name end)
  m.setWorkAllowed = direct(function(s, v)
    if type(v) == "boolean" and not s.ignore_work then
      s.world:after_ticks(1, function()
        s.allowed = v
        if not v then
          s.active = false
        end
      end)
    end
  end)
  m.getSensorInformation = nondirect(function(s)
    local out = {}
    for i, line in ipairs(s.sensor) do
      out[i] = line
    end
    return out
  end)
  -- OC gt_energyContainer (or DriverLSC for an LSC), non-direct
  m.getStoredEU = nondirect(function(s) return s.eu end)
  m.getEUCapacity = nondirect(function(s) return s.eu_max end)
  m.getStoredEUString = nondirect(function(s) return s.stored_str or tostring(s.eu) end)
  m.getEUCapacityString = nondirect(function(s) return s.capacity_str or tostring(s.eu_max) end)
  m.getAverageElectricInput = nondirect(function(s) return s.eu_in end)
  m.getAverageElectricOutput = nondirect(function(s) return s.eu_out end)
  m.getInputVoltage = nondirect(function() return 0 end)
  m.getOutputVoltage = nondirect(function() return 0 end)
  m.getOutputAmperage = nondirect(function() return 0 end)
  m.getStoredSteam = nondirect(function() return 0 end)
  m.getSteamCapacity = nondirect(function() return 0 end)
  for _, name in ipairs(opts.without or {}) do
    m[name] = nil
  end
  return w:add(d, opts.quiet ~= false)
end

-- The LSC: a gt_machine named multimachine.supercapacitor whose string methods give the exact
-- BigInteger, and 24 encoded sensor lines (MTELapotronicSuperCapacitor.getInfoData, 5.09.54.265).
function F.lsc(w, opts)
  opts = opts or {}
  opts.name = "multimachine.supercapacitor"
  opts.stored_str = opts.stored_str or "123456789012345678901234"
  opts.capacity_str = opts.capacity_str or "999999999999999999999999"
  opts.eu = opts.eu or 9223372036854775807
  opts.eu_max = opts.eu_max or 9223372036854775807
  opts.sensor = opts.sensor or {
    "kekztech.infodata.operational_data",
    "kekztech.infodata.lapotronic_super_capacitor.eu_stored\\\\123,456,789,012,345,678,901,234",
    "kekztech.infodata.lapotronic_super_capacitor.eu_stored\\\\1.23 * 10^23",
    "kekztech.infodata.lapotronic_super_capacitor.used_capacity\\\\12.34%",
    "kekztech.infodata.lapotronic_super_capacitor.total_capacity\\\\999,999,999,999,999,999,999,999",
    "kekztech.infodata.lapotronic_super_capacitor.total_capacity\\\\1.00 * 10^24",
    "kekztech.infodata.lapotronic_super_capacitor.passive_loss\\\\1,024",
    "kekztech.infodata.lapotronic_super_capacitor.eu_in\\\\8,192",
    "kekztech.infodata.lapotronic_super_capacitor.eu_out\\\\4,096",
    "kekztech.infodata.lapotronic_super_capacitor.avg_eu_in.sec\\\\8,000\\\\5",
    "kekztech.infodata.lapotronic_super_capacitor.avg_eu_out.sec\\\\4,000\\\\5",
    "kekztech.infodata.lapotronic_super_capacitor.avg_eu_in.min5\\\\7,900",
    "kekztech.infodata.lapotronic_super_capacitor.avg_eu_out.min5\\\\4,100",
    "kekztech.infodata.lapotronic_super_capacitor.avg_eu_in.hour1\\\\7,800",
    "kekztech.infodata.lapotronic_super_capacitor.avg_eu_out.hour1\\\\4,200",
    "kekztech.infodata.lapotronic_super_capacitor.time_to.full\\\\3\\\\hours",
    "kekztech.infodata.multi.maintenance_status.ok",
    "kekztech.infodata.lapotronic_super_capacitor.wireless_mode.disabled",
    "kekztech.infodata.lapotronic_super_capacitor.capacitors\\\\\194\1674UHV\194\167r\\\\0",
    "kekztech.infodata.lapotronic_super_capacitor.capacitors\\\\\194\167eUEV\194\167r\\\\0",
    "kekztech.infodata.lapotronic_super_capacitor.capacitors\\\\\194\1672UIV\194\167r\\\\0",
    "kekztech.infodata.lapotronic_super_capacitor.capacitors\\\\\194\167cUMV\194\167r\\\\0",
    "kekztech.infodata.lapotronic_super_capacitor.wireless_eu\\\\\194\167c0",
    "kekztech.infodata.lapotronic_super_capacitor.wireless_eu\\\\\194\167c0",
  }
  return F.gt_machine(w, opts)
end

-- A battery buffer: DriverBatteryBuffer has priority 2, so it names the component.
function F.battery_buffer(w, opts)
  opts = opts or {}
  opts.type = "gt_batterybuffer"
  opts.name = opts.name or "batterybuffer.16.tier.04"
  opts.eu = opts.eu or 8192
  opts.eu_max = opts.eu_max or 32768
  opts.sensor = opts.sensor or {
    "\194\1679Battery Buffer 16x EV\194\167r",
    "GT5U.infodata.battery_buffer.stored_items\\\\\194\167a1,234,567\194\167r\\\\\194\167e2,000,000\194\167r",
    "GT5U.infodata.battery_buffer.average_input\\\\512",
    "GT5U.infodata.battery_buffer.average_output\\\\300",
  }
  local d = F.gt_machine(w, opts)
  d.methods.getBatteryCharge = nondirect(function(_, slot)
    if slot < 1 or slot > 16 then
      return nil, "slot does not exist"
    end
    return 100000
  end)
  d.methods.getMaxBatteryCharge = nondirect(function(_, slot)
    if slot < 1 or slot > 16 then
      return nil, "slot does not exist"
    end
    return 125000
  end)
  return d
end

-- An AE2 crafting job behind OC's Craftable.request: it plans, then crafts (or fails, or is
-- canceled), and answers through a CraftingStatus as OC 1.12.68's does. w.me:reload_jobs() does
-- what a world save and load do to it: OC keeps the status but not its AE2 link.
local function craft_request(w, s, key, p)
  return nondirect(function(amount, prioritize, cpu)
    if p.no_controller then
      return nil, "no controller"
    end
    local job = { key = key, amount = amount or 1, cpu = cpu, computing = true, failed = false, done = false, canceled = false, reason = "no link" }
    s.requests[#s.requests + 1] = job
    w:after_ticks(p.plan_ticks or 2, function()
      if p.plan_forever or job.reloaded then
        return
      end
      job.computing = false
      if p.fail then
        job.failed = true
        job.reason = "request failed (" .. p.fail .. ")"
      else
        job.link = true
        w:after_ticks(p.craft_ticks or 40, function()
          if p.cancel then
            job.canceled = true
          else
            job.done = true
          end
        end)
      end
    end)
    return w:value("craftingstatus", {
      isComputing = nondirect(function()
        return job.computing
      end),
      hasFailed = nondirect(function()
        return job.failed, job.reason
      end),
      isCanceled = nondirect(function()
        if job.computing then
          return false, "computing"
        end
        if job.link then
          return job.canceled
        end
        return job.failed, job.reason
      end),
      isDone = nondirect(function()
        if job.computing then
          return false, "computing"
        end
        if job.link then
          return job.done
        end
        return not job.failed, job.reason
      end),
    })
  end)
end

-- AE2 through OC's NetworkControl (me_controller, or an ME interface block). Items carry OC's
-- ItemStack converter fields plus AE2's long size and isCraftable; hasTag marks an NBT variant.
-- A name in opts.unknown is one the game doesn't know.
function F.me(w, opts)
  opts = opts or {}
  local d = device(w, opts.type or "me_controller", opts)
  d.items = opts.items or {}
  d.cpus = opts.cpus or { { name = "", storage = 65536, coprocessors = 1, busy = false } }
  d.patterns = opts.patterns or {} -- "name@damage" -> { plan_ticks, fail = reason, craft_ticks, cancel = bool }
  d.unknown = opts.unknown or {}
  d.requests = {}
  -- OC CraftingStatus.save then load: isComputing false, the link gone, failed unless the job
  -- was done, the reason kept ("no link" unless the request had failed).
  function d.reload_jobs(s)
    for _, job in ipairs(s.requests) do
      job.failed = not (job.link and job.done)
      job.computing, job.link, job.reloaded = false, nil, true
    end
  end
  -- OC CraftingStatus.save alone (an autosave, no reload): it sets the live status's failed flag
  -- (true without a link or while the linked job runs) and changes nothing else.
  function d.autosave_jobs(s)
    for _, job in ipairs(s.requests) do
      job.failed = not (job.link and job.done)
    end
  end
  local m = d.methods
  m.getCpus = nondirect(function(s)
    local out = {}
    for i, c in ipairs(s.cpus) do
      out[i] = {
        name = c.name,
        storage = c.storage,
        coprocessors = c.coprocessors,
        busy = c.busy,
        cpu = w:value("cpu", { isBusy = nondirect(function() return c.busy end) }),
      }
    end
    return out
  end)
  -- The agent makes exact lookups only; these walk or convert the whole network on the server
  -- thread (getItemsInNetworkById every variant of a name, getCraftables every craftable).
  for _, name in ipairs({ "getItemsInNetwork", "allItems", "getItemsInNetworkById", "getCraftables" }) do
    m[name] = nondirect(function()
      error("test: " .. name .. " must never be called", 0)
    end)
  end
  -- OC: getItemInNetwork(name|id[, damage[, nbt]]): the stack of that item and damage with the
  -- given NBT (default "{}", which AE2 reads as none), with AE2's merged size and craftable flag;
  -- nil when none is stored and there is no pattern, or the game doesn't know the name.
  m.getItemInNetwork = nondirect(function(s, name, damage, nbt)
    if type(name) == "number" then
      error("test: the agent never sends a numeric id (EndlessIDs)", 0)
    end
    if type(name) ~= "string" then
      error("bad argument #1 (number expected, got " .. type(name) .. ")", 0)
    end
    damage = damage or 0
    if nbt ~= nil and nbt ~= "{}" then
      error("test: the agent looks up the stack without NBT", 0)
    end
    if s.unknown[name] then
      return nil
    end
    local size, craftable, found = 0, s.patterns[name .. "@" .. damage] ~= nil, false
    for _, it in ipairs(s.items) do
      if it.name == name and it.damage == damage and not it.hasTag then
        size, found = size + it.size, true
        craftable = craftable or it.isCraftable == true
      end
    end
    if not found and not craftable then
      return nil
    end
    return { name = name, damage = damage, size = size, isCraftable = craftable, label = name, hasTag = false, maxDamage = 0, maxSize = 64 }
  end)
  -- OC: getCraftable(detail, type): the pattern for exactly that stack (no NBT), or nil; an item
  -- name the game doesn't know is an error ("item not found"), as is a type OC didn't register.
  m.getCraftable = nondirect(function(s, detail, tp)
    if type(detail) ~= "table" then
      error("bad argument #1 (table expected)", 0)
    end
    if type(tp) ~= "string" then
      error("bad argument #2 (string expected)", 0)
    end
    if tp ~= "item" and tp ~= "fluid" then
      error("Type " .. tp .. " hasn't been registered", 0)
    end
    if type(detail.name) ~= "string" or s.unknown[detail.name] then
      error("item not found", 0)
    end
    local damage = detail.damage or 0
    local key = detail.name .. "@" .. damage
    local p = s.patterns[key]
    if not p then
      return nil
    end
    return w:value("craftable", {
      getStack = nondirect(function()
        return { name = detail.name, damage = damage, size = 1 }
      end),
      request = craft_request(w, s, key, p),
    })
  end)
  m.getStoredPower = nondirect(function() return 1000.0 end)
  m.getMaxStoredPower = nondirect(function() return 8000.0 end)
  m.getAvgPowerUsage = nondirect(function() return 20.0 end)
  m.getAvgPowerInjection = nondirect(function() return 40.0 end)
  m.getIdlePowerUsage = nondirect(function() return 10.0 end)
  m.getFluidsInNetwork = nondirect(function() return {} end)
  m.setItemEventSubscription = nondirect(function() end)
  m.isItemEventSubscription = direct(function() return false end)
  return w:add(d, opts.quiet ~= false)
end

-- An ME interface on a cable (a part): OC 1.12.68 gives it no network methods.
function F.me_interface_part(w, opts)
  opts = opts or {}
  local d = device(w, "me_interface", opts)
  d.methods.getInterfaceConfiguration = nondirect(function() return nil end)
  return w:add(d, opts.quiet ~= false)
end

function F.chat_box(w, opts)
  opts = opts or {}
  local d = device(w, "chat_box", opts)
  d.said = {}
  d.methods.say = nondirect(function(s, text, distance)
    if type(text) ~= "string" then
      return false
    end
    s.said[#s.said + 1] = text
    return true
  end)
  d.methods.getName = direct(function() return "" end)
  d.methods.setName = direct(function(_, n) return n end)
  d.methods.getDistance = direct(function() return 40 end)
  return w:add(d, opts.quiet ~= false)
end

function F.tps_card(w, opts)
  opts = opts or {}
  local d = device(w, "tps_card", opts)
  d.tick_ms = opts.tick_ms or 12.345
  d.methods.getOverallTickTime = nondirect(function(s) return s.tick_ms end)
  d.methods.convertTickTimeIntoTps = nondirect(function(_, ms) return math.min(20, 1000 / ms) end)
  return w:add(d, opts.quiet ~= false)
end

-- A screen and a GPU (WO-7.4), as OC 1.12.68 behaves for what the board uses: every call is
-- direct; the GPU draws into the screen it is bound to; its resolution is at most the smaller of
-- its own and the screen's maximum (tier 1 50x16, tier 2 80x25, tier 3 160x50). The screen keeps
-- its rows as text (s.rows[y]) and the foreground each set used (s.fg[y]); g.sets counts set calls.
function F.screen(w, opts)
  opts = opts or {}
  local d = device(w, "screen", opts)
  d.max_w, d.max_h = opts.w or 80, opts.h or 25
  d.res_w, d.res_h = d.max_w, d.max_h
  d.rows, d.fg = {}, {}
  d.methods.getAspectRatio = direct(function() return 1, 1 end)
  return w:add(d, opts.quiet ~= false)
end

function F.gpu(w, opts)
  opts = opts or {}
  local d = device(w, "gpu", opts)
  d.max_w, d.max_h = opts.w or 160, opts.h or 50
  d.fg_color, d.bg_color = 0xFFFFFF, 0x000000
  d.sets, d.fills = 0, 0
  local function scr(s)
    return s.bound and w.devices[s.bound] or nil
  end
  d.methods.bind = direct(function(s, addr)
    local sd = w.devices[addr]
    if not sd or sd.type ~= "screen" then
      return nil, "invalid address"
    end
    s.bound = addr
    return true
  end)
  d.methods.getScreen = direct(function(s) return s.bound end)
  d.methods.maxResolution = direct(function(s)
    local sd = scr(s)
    if not sd then
      return nil, "no screen"
    end
    return math.min(s.max_w, sd.max_w), math.min(s.max_h, sd.max_h)
  end)
  d.methods.getResolution = direct(function(s)
    local sd = scr(s)
    return sd and sd.res_w, sd and sd.res_h
  end)
  d.methods.setResolution = direct(function(s, rw, rh)
    local sd = scr(s)
    if not sd then
      return nil, "no screen"
    end
    if rw < 1 or rh < 1 or rw > math.min(s.max_w, sd.max_w) or rh > math.min(s.max_h, sd.max_h) then
      error("unsupported resolution", 0)
    end
    sd.res_w, sd.res_h = rw, rh
    return true
  end)
  d.methods.setForeground = direct(function(s, c)
    local old = s.fg_color
    s.fg_color = c
    return old
  end)
  d.methods.setBackground = direct(function(s, c)
    local old = s.bg_color
    s.bg_color = c
    return old
  end)
  d.methods.fill = direct(function(s, x, y, fw, fh, ch)
    local sd = scr(s)
    if not sd then
      return nil, "no screen"
    end
    s.fills = s.fills + 1
    for row = y, y + fh - 1 do
      if row >= 1 and row <= sd.res_h then
        sd.rows[row] = (ch):rep(sd.res_w)
        sd.fg[row] = s.fg_color
      end
    end
    return true
  end)
  d.methods.set = direct(function(s, x, y, text)
    local sd = scr(s)
    if not sd then
      return nil, "no screen"
    end
    s.sets = s.sets + 1
    if y < 1 or y > sd.res_h then
      return true
    end
    local row = sd.rows[y] or (" "):rep(sd.res_w)
    local line = (row:sub(1, x - 1) .. text .. row:sub(x + #text)):sub(1, sd.res_w)
    sd.rows[y] = line .. (" "):rep(sd.res_w - #line)
    sd.fg[y] = s.fg_color
    sd.overflow = (sd.overflow or 0) + math.max(0, x - 1 + #text - sd.res_w)
    return true
  end)
  return w:add(d, opts.quiet ~= false)
end

-- An OCGlasses 1.6.9-GTNH terminal (WO-7.4): addTextLabel returns a widget whose setters are
-- called with a dot; every add, remove and setter is one packet to the bound players (g.packets),
-- and the terminal draws widgets/10 OC energy a tick [F 4.1] (g:energy_per_tick()).
function F.glasses(w, opts)
  opts = opts or {}
  local d = device(w, "glasses", opts)
  d.widgets, d.packets, d.next_id = {}, 0, 0
  function d.energy_per_tick(s)
    local n = 0
    for _ in pairs(s.widgets) do
      n = n + 1
    end
    return n / 10
  end
  d.methods.addTextLabel = direct(function(s)
    s.next_id = s.next_id + 1
    local id = s.next_id
    local wd = { id = id, text = "", x = 0, y = 0, color = { 1, 1, 1 }, scale = 1 }
    s.widgets[id] = wd
    s.packets = s.packets + 1
    local function setter(field)
      return function(...)
        if s.widgets[id] == nil then
          error("widget removed", 0)
        end
        local v = table.pack(...)
        if field == "text" then
          wd.text = v[1]
        elseif field == "pos" then
          wd.x, wd.y = v[1], v[2]
        elseif field == "color" then
          wd.color = { v[1], v[2], v[3] }
        else
          wd.scale = v[1]
        end
        s.packets = s.packets + 1
      end
    end
    return {
      setText = setter("text"),
      setPosition = setter("pos"),
      setColor = setter("color"),
      setScale = setter("scale"),
      getID = function() return id end,
    }
  end)
  d.methods.removeObject = direct(function(s, id)
    s.widgets[id] = nil
    s.packets = s.packets + 1
    return true
  end)
  d.methods.removeAll = direct(function(s)
    s.widgets = {}
    s.packets = s.packets + 1
    return true
  end)
  d.methods.getObjectCount = direct(function(s)
    local n = 0
    for _ in pairs(s.widgets) do
      n = n + 1
    end
    return n
  end)
  return w:add(d, opts.quiet ~= false)
end

function F.plain(w, dtype, opts)
  opts = opts or {}
  local d = device(w, dtype, opts)
  return w:add(d, opts.quiet ~= false)
end

-- The internet card. w.server(req, w) decides each answer; its fields:
--   fail = "IOException text"  finishConnect (and read) return nil and it, as for a refused
--                               connection, a filtered address ("address is not allowed") or a
--                               code of 400 or more
--   lost_after = n              the connection is lost after n chunks were read (a world reload)
--   code, message               response() (default 200, "OK")
--   body                        the reply text
--   pieces = {n, ...}           split the body into pieces of these sizes as they arrive
--   gaps = n                    "" reads (no data yet) before each piece
--   ready_ticks = n             ticks before finishConnect is true (default 2)
--   stall_connect / stall_read  never connect / never end the body
function F.internet_card(w, opts)
  opts = opts or {}
  local d = device(w, "internet", opts)
  d.open = {}
  d.max_open = 4
  local function nopen()
    local n = 0
    for _ in pairs(d.open) do
      n = n + 1
    end
    return n
  end
  d.methods.isHttpEnabled = direct(function() return true end)
  d.methods.request = nondirect(function(s, url, post, headers, method)
    if w.inet_unavailable then
      return nil, "internet access is unavailable"
    end
    if nopen() >= s.max_open then
      error("too many open connections", 0)
    end
    local req = { url = url, body = post, headers = headers or {}, method = method, t = w.clock }
    w.nrequests = (w.nrequests or 0) + 1
    if w.on_request then
      w.on_request(post) -- the runner's --dump: every body, even in a lean world
    end
    if w.lean then
      w.requests = { req } -- a long run keeps only the last request
    else
      w.requests[#w.requests + 1] = req
    end
    local spec = w.server and w.server(req, w) or { body = '{"v":1,"ok":true,"cmds":[]}' }
    req.spec = spec
    if spec.body and not w.lean then
      w.replies[#w.replies + 1] = spec.body
    end
    local ready_at = w.clock + (spec.ready_ticks or 2) * TICK
    local pieces, pos = {}, 1
    local body = spec.body or ""
    for _, n in ipairs(spec.pieces or { #body }) do
      pieces[#pieces + 1] = body:sub(pos, pos + n - 1)
      pos = pos + n
    end
    if pos <= #body then
      pieces[#pieces + 1] = body:sub(pos)
    end
    local gaps_left = spec.gaps or 0
    local h = {}
    s.open[h] = true
    local function ready()
      return not spec.stall_connect and w.clock >= ready_at
    end
    local v = w:value("request", {
      finishConnect = nondirect(function()
        if not s.open[h] then
          return nil, "connection lost"
        end
        if not ready() then
          return false
        end
        if spec.fail then
          return nil, spec.fail
        end
        return true
      end),
      response = direct(function()
        if not ready() or spec.fail then
          return nil
        end
        return spec.code or 200, spec.message or "OK", {}
      end),
      read = nondirect(function(n)
        if not s.open[h] or (spec.lost_after and (req.reads or 0) >= spec.lost_after) then
          return nil, "connection lost"
        end
        n = math.min(2048, math.max(0, n or math.huge))
        req.max_read = math.max(req.max_read or 0, n)
        if not ready() then
          return ""
        end
        if spec.fail then
          return nil, spec.fail
        end
        if spec.stall_read then
          return ""
        end
        if gaps_left > 0 then
          gaps_left = gaps_left - 1
          return ""
        end
        local piece = pieces[1]
        if not piece then
          return nil
        end
        local out = piece:sub(1, n)
        if #out < #piece then
          pieces[1] = piece:sub(n + 1)
        else
          table.remove(pieces, 1)
          gaps_left = spec.gaps or 0
        end
        req.reads = (req.reads or 0) + 1
        return out
      end),
      close = direct(function()
        s.open[h] = nil
        req.closed = true
      end),
    })
    return v
  end)
  d.nopen = nopen
  return w:add(d, opts.quiet ~= false)
end

---------------------------------------------------------------------------------------------
-- Libraries

function F.component_lib(w)
  local lib = {}
  function lib.list(filter, exact)
    local list = {}
    for addr, d in pairs(w.devices) do
      if not filter or (exact and d.type == filter) or (not exact and d.type:find(filter, 1, true)) then
        list[addr] = d.type
      end
    end
    local key
    return setmetatable(list, {
      __call = function()
        key = next(list, key)
        if key then
          return key, list[key]
        end
      end,
    })
  end
  function lib.type(addr)
    local d = w.devices[addr]
    if not d then
      return nil, "no such component"
    end
    return d.type
  end
  function lib.methods(addr)
    local d = w.devices[addr]
    if not d then
      return nil, "no such component"
    end
    local out = {}
    for name, m in pairs(d.methods) do
      out[name] = m.direct
    end
    return out
  end
  function lib.invoke(addr, method, ...)
    return w:invoke(addr, method, ...)
  end
  function lib.proxy(addr)
    local d = w.devices[addr]
    if not d then
      return nil, "no such component"
    end
    local p = { address = addr, type = d.type }
    for name in pairs(d.methods) do
      p[name] = function(...)
        return w:invoke(addr, name, ...)
      end
    end
    return p
  end
  local function primary(ctype)
    local best
    for addr, d in pairs(w.devices) do
      if d.type == ctype and (not best or addr < best) then
        best = addr
      end
    end
    return best
  end
  function lib.isAvailable(ctype)
    return primary(ctype) ~= nil
  end
  function lib.getPrimary(ctype)
    local addr = primary(ctype)
    if not addr then
      error("no primary '" .. ctype .. "' available", 2)
    end
    return lib.proxy(addr)
  end
  return setmetatable(lib, {
    __index = function(_, k)
      if primary(k) then
        return lib.getPrimary(k)
      end
    end,
  })
end

function F.computer_lib(w)
  return {
    uptime = function()
      return w.clock
    end,
    address = function()
      return w.address
    end,
    freeMemory = function()
      return w.mem_free
    end,
    totalMemory = function()
      return w.mem_total
    end,
    energy = function()
      return w.energy
    end,
    maxEnergy = function()
      return w.energy_max
    end,
    getArchitecture = function()
      return w.arch
    end,
    tmpAddress = function()
      return w.tmp_address
    end,
    pushSignal = function(name, ...)
      w:push(name, ...)
      return true
    end,
    pullSignal = function(timeout)
      return w:pull_signal(timeout)
    end,
    beep = function() end,
  }
end

function F.event_lib(w)
  local lib = {}
  local function matches(sig, name, ...)
    if name and not (type(sig[1]) == "string" and sig[1]:match(name)) then
      return false
    end
    local filter = table.pack(...)
    for i = 1, filter.n do
      if filter[i] ~= nil and filter[i] ~= sig[i + 1] then
        return false
      end
    end
    return true
  end
  function lib.pull(...)
    local args = table.pack(...)
    local timeout, name, rest
    if type(args[1]) == "string" then
      name, rest = args[1], table.pack(select(2, ...))
    else
      timeout, name, rest = args[1], args[2], table.pack(select(3, ...))
    end
    local deadline = timeout and (w.clock + timeout) or math.huge
    repeat
      local sig = w:pull(deadline == math.huge and nil or math.max(0, deadline - w.clock))
      if sig.n > 0 and matches(sig, name, table.unpack(rest, 1, rest.n)) then
        return table.unpack(sig, 1, sig.n)
      end
    until w.clock >= deadline
  end
  function lib.listen(name, cb)
    w.listeners[name] = w.listeners[name] or {}
    for _, c in ipairs(w.listeners[name]) do
      if c == cb then
        return false
      end
    end
    table.insert(w.listeners[name], cb)
    return true
  end
  function lib.ignore(name, cb)
    for i, c in ipairs(w.listeners[name] or {}) do
      if c == cb then
        table.remove(w.listeners[name], i)
        return true
      end
    end
    return false
  end
  lib.push = function(name, ...)
    w:push(name, ...)
  end
  return lib
end

local function norm(path)
  local parts = {}
  for seg in path:gmatch("[^/]+") do
    if seg == ".." then
      parts[#parts] = nil
    elseif seg ~= "." then
      parts[#parts + 1] = seg
    end
  end
  return "/" .. table.concat(parts, "/")
end

function F.filesystem_lib(w)
  local fs = {}
  local function parent(p)
    return p:match("^(.*)/[^/]*$") or "/"
  end
  function fs.canonical(p)
    return norm(p)
  end
  function fs.exists(p)
    p = norm(p)
    return w.files[p] ~= nil or w.dirs[p] == true
  end
  function fs.isDirectory(p)
    return w.dirs[norm(p)] == true
  end
  function fs.makeDirectory(p)
    p = norm(p)
    local acc = ""
    for seg in p:gmatch("[^/]+") do
      acc = acc .. "/" .. seg
      if not w.dirs[acc] then
        w.dirs[acc] = true
        w.used = w.used + w.file_cost
      end
    end
    return true
  end
  -- OpenOS: the drive's proxy and its mount path. The test base's boot drive is at /, the RAM
  -- disk at /tmp; w.mounts replaces both.
  function fs.get(p)
    p = norm(p)
    local mounts = w.mounts or {
      ["/"] = { address = w.boot_fs or "0f000000-0000-4000-8000-0000000000f0" },
      ["/tmp"] = { address = w.tmp_address },
    }
    local best
    for mount in pairs(mounts) do
      local inside = mount == "/" or p == mount or p:sub(1, #mount + 1) == mount .. "/"
      if inside and (not best or #mount > #best) then
        best = mount
      end
    end
    if not best then
      return nil, "no such file system"
    end
    local m = mounts[best]
    return {
      address = m.address,
      isReadOnly = function()
        return m.readonly == true
      end,
    }, best
  end
  function fs.size(p)
    local f = w.files[norm(p)]
    return f and #f or 0
  end
  function fs.lastModified(p)
    return w.mtimes[norm(p)] or 0
  end
  -- Not direct in OC: one tick. OC subtracts what it frees (fileCost and the size), whoever
  -- wrote the file.
  function fs.remove(p)
    w:count("filesystem", "remove", false)
    w:tick()
    p = norm(p)
    if w.fail.remove and w.fail.remove > 0 then
      w.fail.remove = w.fail.remove - 1
      return false
    end
    if w.files[p] then
      w.used = w.used - w.file_cost - #w.files[p]
      w.files[p] = nil
      return true
    end
    return nil, "no such file or directory"
  end
  -- Not direct in OC: one tick.
  function fs.rename(a, b)
    w:count("filesystem", "rename", false)
    w:tick()
    a, b = norm(a), norm(b)
    if w.fail.rename and w.fail.rename > 0 then
      w.fail.rename = w.fail.rename - 1
      return false
    end
    if not w.files[a] then
      return nil, "no such file or directory"
    end
    w.files[b], w.files[a] = w.files[a], nil
    w.mtimes[b] = w.clock * 1000
    return true
  end
  function fs.list(p)
    p = norm(p)
    local names = {}
    for path in pairs(w.files) do
      if parent(path) == p then
        names[#names + 1] = path:match("[^/]+$")
      end
    end
    table.sort(names)
    local i = 0
    return function()
      i = i + 1
      return names[i]
    end
  end
  function fs.open(p, mode)
    mode = mode or "r"
    p = norm(p)
    if mode == "r" or mode == "rb" then
      if not w.files[p] then
        return nil, "file not found"
      end
    elseif not w.dirs[parent(p)] then
      return nil, "file not found"
    elseif w.fail.open_write and w.fail.open_write > 0 then
      w.fail.open_write = w.fail.open_write - 1
      return nil, "not enough space"
    else
      -- OC's Capacity: a new file costs fileCost; mode w subtracts the old size first.
      local truncate = mode == "w" or mode == "wb"
      local delta = w.files[p] and (truncate and -#w.files[p] or 0) or w.file_cost
      if w.capacity and w.capacity - w.used < delta then
        return nil, "not enough space"
      end
      w.used = w.used + delta
      if truncate then
        w.files[p] = ""
      else
        w.files[p] = w.files[p] or ""
      end
      w.mtimes[p] = w.clock * 1000
    end
    local pos, closed = 1, false
    local h = {}
    function h:read(n)
      if closed then
        return nil, "file is closed"
      end
      n = math.min(2048, n or math.huge)
      w.fs_reads[p] = (w.fs_reads[p] or 0) + 1
      local data = w.files[p] or ""
      if pos > #data then
        return nil
      end
      local out = data:sub(pos, pos + n - 1)
      pos = pos + #out
      return out
    end
    function h:write(s)
      if closed then
        return nil, "file is closed"
      end
      if w.capacity and w.capacity - w.used < #s then
        return nil, "not enough space"
      end
      w.used = w.used + #s
      w.files[p] = (w.files[p] or "") .. s
      w.mtimes[p] = w.clock * 1000
      return true
    end
    function h:close()
      closed = true
    end
    return h
  end
  return fs
end

-- OpenOS's internet.request over the fake card: a handle callable as a chunk iterator, with the
-- request's own methods (finishConnect, response, read) and close.
function F.internet_lib(w)
  local lib = {}
  function lib.request(url, data, headers, method)
    local addr
    for a, d in pairs(w.devices) do
      if d.type == "internet" and (not addr or a < addr) then
        addr = a
      end
    end
    if not addr then
      error("no primary internet card found", 2)
    end
    local req, reason = w:invoke(addr, "request", url, data, headers, method)
    if not req then
      error(reason, 2)
    end
    return setmetatable({
      close = setmetatable({}, {
        __call = function()
          return req.close()
        end,
      }),
    }, {
      __call = function()
        while true do
          local chunk, why = req.read()
          if not chunk then
            req.close()
            if why then
              error(why, 2)
            end
            return nil
          elseif #chunk > 0 then
            return chunk
          end
          w:sleep(0)
        end
      end,
      __index = req,
    })
  end
  return lib
end

---------------------------------------------------------------------------------------------
-- Loading the agent into a world

local AGENT = "oc/gos-agent.lua"
F.ROOT = "."

function F.set_root(root)
  F.ROOT = root
  AGENT = root .. "/oc/gos-agent.lua"
end

-- Install this world's libraries and load a fresh agent module (gos-agent.lua --lib).
function F.load_agent(w, opts)
  opts = opts or {}
  package.loaded.component = w.component
  package.loaded.computer = w.computer
  package.loaded.event = w.event
  package.loaded.filesystem = w.filesystem
  package.loaded.internet = w.internet
  local env = setmetatable({
    print = function(...)
      w:print(...)
    end,
    os = w.os,
    _VERSION = opts.version or _VERSION,
  }, { __index = _G })
  local chunk = assert(loadfile(AGENT, "t", env))
  return chunk("--lib")
end

-- A config file for the agent in the fake drive; returns its path.
function F.write_config(w, text, path)
  path = path or "/home/gos/gos-agent.cfg"
  w.filesystem.makeDirectory(path:match("^(.*)/"))
  w.files[path] = text
  return path
end

return F
