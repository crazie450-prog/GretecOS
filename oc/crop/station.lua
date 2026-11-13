--[[ crop/station.lua: the breeding station (WO-7.6; plan 7.17 item 4, D78, R28, V31; contract
gos-oc-bridge/1, "The breeding station", in docs/contracts/oc_bridge.md; D282-D284). Written for
this repo (MIT, see LICENSE at the repo root).

gos-agent.lua loads this module when its config has a `crops` table. It runs a breeding plan GOS
built (gregtec.crops.station) on CropsNH's Crop Breeder and Seed Generators through ONE transposer
that touches a storage chest, the Crop Breeder and one to three Seed Generators, so every move is
one transferItem between the chest and a machine: atomic, never an item between two blocks.

What GT allows shapes everything here (GT 5.09.54.265 MTEBasicMachine.allowPullStack): automation
may take items only from a basic machine's OUTPUT slot. A seed put in an input slot stays there
until the machine uses it or a player takes it. So:

  copiers  a Seed Generator never uses up the seed it copies (MTESeedGenerator.checkRecipe), so a
           seed put into one stays as that generator's master for good: each generator is the
           copier of one crop. The plan's `copy` list ranks the crops worth a copier (the target
           first, then the parents used most); an empty generator takes the first of them that
           has no copier yet, when a seed of it is in the chest. That parked seed is never bred:
           it keeps making copies. A copier runs (setWorkAllowed true) only while its crop is
           short and is paused otherwise.
  breeder  a run takes ONE of each parent (and the recipe's catalysts) when it starts and, at
           its end, puts the child in the output slot on a success and nothing on a failure
           (MTECropBreeder.checkRecipe). The station loads it only when it is idle and empty,
           its enriched fertilizer covers the run (transposer getTankLevel; GOS computes the mB
           from the breeding maths), and the chest holds keep + 1 of every parent, so loading
           one leaves `keep` (at least 1) behind: it never uses the last seed of a parent. It is
           paused while loading (so no smaller recipe starts on a half-loaded set) and resumed.
           A seed of the step's child in the output is success; the run ending with an empty
           output is a failure, and the next look finds the parents short again.
  demand   every step asks what the target needs: `count` of it in the chest. A crop that is
           short is copied (its copier), else bred again (the step whose child it is, after its
           own parents are there: before a step's first run, `copies` of each parent when a
           copier makes them, else keep + 1), else the station waits and says what is missing.
           Failures need no special case: the parents a failed run ate are short on the next
           look.

Seeds are told apart by item name and label only: CropsNH's one seed item, cropsnh:genericSeed,
whose label is "<crop name> Seeds" once the seed is analysed (ItemGenericSeed: "Unknown Seeds"
before, which the breeder refuses). Never a numeric id, never a planted crop (CropsNH removed OC
support for planted crops). Stats aren't read: OC's `tag` is CompressedStreamTools' gzip, which
the data card's zlib inflate can't open (V31), so GOS tracks them from the breeding maths.

A stop starts no new work: the copiers are paused, a run under way is waited for and its output
collected, the copiers' last copies are collected, and the machines' work switches are put back
as they were. Then no item the station moved is anywhere but the chest, except the copiers'
parked seeds (which stay by design and are listed). Start and stop are commands Clayton
confirmed (D59); this module never starts itself: after a restart a saved run is `interrupted`
until a confirmed start resumes it or a confirmed stop winds it down.

  local s, err = station.new(cfg, deps)  cfg from station.check_config(gos-agent.cfg's crops)
       deps: call(addr, method, ...) -> ok, ..., now(), log(level, msg), json,
             list(type) -> { addr = type }, read(path) -> text, write(path, text) -> ok, err
  s:start(id, args) -> { ok, state, err }   a confirmed crop_start's args (the plan)
  s:stop(id)        -> { ok, state, err }   a confirmed crop_stop
  s:step()          one slice of work (a few server ticks); the agent calls it every step_s
  s:doc()           the telemetry's `crops`
  s.next_at         uptime of the next step

Keep this file valid Lua 5.2, like gos-agent.lua (the tests compile it with luac5.2).
]]

local S = { VERSION = "0.1.0" }

S.SEED = "cropsnh:genericSeed"
local SEED = S.SEED
local BREEDER_NAME = "^basicmachine%.cropbreeder%.tier%.(%d+)$"
local GENERATOR_NAME = "^basicmachine%.seedgenerator%.tier%.(%d+)$"
-- GT 5.09.54.265 MTEBasicMachine: 5 slots before the inputs (OTHER_SLOT_COUNT), the inputs, the
-- outputs, then one special slot. 1-based for OC: the inputs start at slot 6.
local FIRST_INPUT = 6
local OTHER_SLOTS = 5
local MAX_STEPS = 8
local MAX_PARENTS = 4
local MAX_LABEL = 64
local MAX_NOTE = 200
local MAX_COPIES = 64
local MAX_COUNT = 64
local MAX_CAT = 4
local MAX_GENERATORS = 3
local MAX_COPY = 8
local LIVE = { running = true, stopping = true }

S.DEFAULTS = {
  transposer = false, -- address prefix; default: the only transposer
  storage = false, -- the transposer's side (0-5) facing the storage chest
  breeder = false, -- its side facing the Crop Breeder
  generators = false, -- the Seed Generators: { { side = 3, adapter = "ab12" }, ... } (1 to 3)
  step_s = 2, -- seconds between two steps
  start_s = 30, -- a loaded breeder that hasn't taken its inputs by then is reported
  run_max_s = 1800, -- a run longer than this is reported as stuck (it is still waited for)
  keep = 1, -- seeds of each parent always left in the chest (at least 1)
  state = true, -- true: gos-crops.state next to the config; false: none; or a path
}

local RANGES = {
  step_s = { 1, 10 },
  start_s = { 5, 600 },
  run_max_s = { 60, 86400 },
  keep = { 1, 8 },
}

local function int(v)
  return math.type and math.type(v) == "integer" and v or nil
end

local function side(v)
  local n = int(v)
  return n and n >= 0 and n <= 5 and n or nil
end

local function clip(s, n)
  s = tostring(s)
  if #s > n then
    return s:sub(1, n - 3) .. "..."
  end
  return s
end

local function prefix_ok(p)
  return type(p) == "string" and #p >= 3 and #p <= 36 and p:match("^[%x%-]+$") ~= nil
end

function S.check_config(t, base)
  if type(t) ~= "table" then
    return nil, "crops must be a table of breeding station options"
  end
  local cfg = {}
  for k, v in pairs(S.DEFAULTS) do
    cfg[k] = v
  end
  for k, v in pairs(t) do
    if S.DEFAULTS[k] == nil then
      return nil, "crops: unknown option " .. tostring(k)
    end
    cfg[k] = v
  end
  if not side(cfg.storage) or not side(cfg.breeder) then
    return nil, "crops.storage and crops.breeder must be the transposer's sides (0 to 5) facing them"
  end
  local used = { [cfg.storage] = "storage" }
  if used[cfg.breeder] then
    return nil, "crops: storage and breeder must be different sides"
  end
  used[cfg.breeder] = "breeder"
  if type(cfg.generators) ~= "table" or #cfg.generators < 1 or #cfg.generators > MAX_GENERATORS then
    return nil, "crops.generators must list 1 to " .. MAX_GENERATORS .. " Seed Generators: { { side = 3, adapter = \"ab12\" } }"
  end
  local gens = {}
  for i, g in ipairs(cfg.generators) do
    if type(g) ~= "table" or not side(g.side) or used[g.side] then
      return nil, ("crops.generators[%d].side must be a side (0 to 5) no other part uses"):format(i)
    end
    used[g.side] = "generator"
    if g.adapter ~= nil and not prefix_ok(g.adapter) then
      return nil, ("crops.generators[%d].adapter must be its adapter's address prefix (3+ hex characters)"):format(i)
    end
    if g.adapter == nil and #cfg.generators > 1 then
      return nil, ("crops.generators[%d].adapter: with more than one generator, name each one's adapter"):format(i)
    end
    gens[i] = { side = g.side, adapter = g.adapter }
  end
  cfg.generators = gens
  if cfg.transposer ~= false and not prefix_ok(cfg.transposer) then
    return nil, "crops.transposer must be an address prefix (3+ hex characters) or false"
  end
  for k, r in pairs(RANGES) do
    local v = cfg[k]
    if type(v) ~= "number" or v < r[1] or v > r[2] then
      return nil, ("crops.%s must be a number from %s to %s"):format(k, r[1], r[2])
    end
  end
  if int(cfg.keep) == nil then
    return nil, "crops.keep must be a whole number"
  end
  if cfg.state == true then
    cfg.state_path = (base or ".") .. "/gos-crops.state"
  elseif type(cfg.state) == "string" then
    cfg.state_path = cfg.state
  elseif cfg.state ~= false then
    return nil, "crops.state must be true, false or a path"
  end
  return cfg
end

---------------------------------------------------------------------------------------------
-- The plan (a confirmed crop_start's args): checked again here, as the agent checks every command

local function label_ok(s)
  return type(s) == "string" and #s >= 7 and #s <= MAX_LABEL and s:sub(-6) == " Seeds" and not s:find("%c")
    and s ~= "Unknown Seeds" and s ~= "Invalid Seeds"
end
S.label_ok = label_ok

local function items(list, what)
  if list == nil then
    return {}
  end
  if type(list) ~= "table" or #list > MAX_CAT then
    return nil, what .. " must be a list of at most " .. MAX_CAT .. " items"
  end
  local out = {}
  for i, c in ipairs(list) do
    if type(c) ~= "table" or type(c.name) ~= "string" or not c.name:match("^[%w_%.%-]+:[^%s%c]+$") or #c.name > 128
      or not int(c.damage) or c.damage < 0 or c.damage > 32767 or not int(c.n) or c.n < 1 or c.n > 64 then
      return nil, what .. " " .. i .. " must be {name = registry name, damage, n = 1..64}"
    end
    out[i] = { name = c.name, damage = c.damage, n = c.n }
  end
  return out
end

function S.check_plan(a)
  if type(a) ~= "table" then
    return nil, "args must be an object"
  end
  if type(a.plan) ~= "string" or not a.plan:match("^%x%x%x%x%x%x%x%x$") then
    return nil, "plan must be the plan's 8-hex id"
  end
  if not label_ok(a.target) then
    return nil, "target must be an analysed seed's label (<crop> Seeds)"
  end
  if not int(a.count) or a.count < 1 or a.count > MAX_COUNT then
    return nil, "count must be 1 to " .. MAX_COUNT
  end
  if type(a.steps) ~= "table" or #a.steps < 1 or #a.steps > MAX_STEPS then
    return nil, "steps must be a list of 1 to " .. MAX_STEPS .. " Crop Breeder steps"
  end
  local plan = { id = a.plan, target = a.target, count = a.count, steps = {}, copy = {}, copy_cat = {}, by_child = {} }
  for i, s in ipairs(a.steps) do
    if type(s) ~= "table" or not label_ok(s.child) then
      return nil, "step " .. i .. ": child must be a seed label"
    end
    if plan.by_child[s.child] then
      return nil, "step " .. i .. ": another step already breeds " .. s.child
    end
    if type(s.parents) ~= "table" or #s.parents < 2 or #s.parents > MAX_PARENTS then
      return nil, "step " .. i .. ": 2 to " .. MAX_PARENTS .. " parents"
    end
    local seen = {}
    for _, p in ipairs(s.parents) do
      if not label_ok(p) or seen[p] or p == s.child then
        return nil, "step " .. i .. ": parents must be distinct seed labels, none the child"
      end
      seen[p] = true
    end
    local cat, err = items(s.cat, "step " .. i .. " catalyst")
    if not cat then
      return nil, err
    end
    if not int(s.copies) or s.copies < 2 or s.copies > MAX_COPIES then
      return nil, "step " .. i .. ": copies must be 2 to " .. MAX_COPIES
    end
    if not int(s.fert) or s.fert < 1 or s.fert > 256000 then
      return nil, "step " .. i .. ": fert must be the run's enriched fertilizer in mB"
    end
    plan.steps[i] = { child = s.child, parents = s.parents, cat = cat, copies = s.copies, fert = s.fert }
    plan.by_child[s.child] = i
  end
  if plan.steps[#plan.steps].child ~= plan.target then
    return nil, "the last step must breed the target"
  end
  if a.copy ~= nil then
    if type(a.copy) ~= "table" or #a.copy > MAX_COPY then
      return nil, "copy must list at most " .. MAX_COPY .. " seed labels"
    end
    for i, l in ipairs(a.copy) do
      if not label_ok(l) then
        return nil, "copy " .. i .. " must be a seed label"
      end
      plan.copy[i] = l
    end
  end
  if a.copy_cat ~= nil then
    if type(a.copy_cat) ~= "table" then
      return nil, "copy_cat must map seed labels to catalyst lists"
    end
    for k, v in pairs(a.copy_cat) do
      if not label_ok(k) then
        return nil, "copy_cat keys must be seed labels"
      end
      local cat, err = items(v, "copy_cat " .. k)
      if not cat then
        return nil, err
      end
      plan.copy_cat[k] = cat
    end
  end
  return plan
end

---------------------------------------------------------------------------------------------
-- Inventories

local function is_seed(st, label)
  return st and st.name == SEED and st.label == label
end

local function count(inv, label)
  local c = 0
  for i = 1, inv.n do
    if is_seed(inv[i], label) then
      c = c + inv[i].size
    end
  end
  return c
end

local function count_item(inv, name, damage)
  local c = 0
  for i = 1, inv.n do
    local st = inv[i]
    if st and st.name == name and st.damage == damage then
      c = c + st.size
    end
  end
  return c
end

local function find_slot(inv, pred, first, last)
  for i = first or 1, last or inv.n do
    if inv[i] and pred(inv[i]) then
      return i
    end
  end
end

-- A machine's layout from its inventory size (MTEBasicMachine: 5 + inputs + 1 output + 1 special).
local function layout(inv)
  local nin = inv.n - OTHER_SLOTS - 2
  return { first = FIRST_INPUT, last = FIRST_INPUT + nin - 1, out = FIRST_INPUT + nin, nin = nin }
end

local function inputs_empty(inv)
  for i = inv.at.first, inv.at.last do
    if inv[i] then
      return false
    end
  end
  return true
end

---------------------------------------------------------------------------------------------
-- The station

local St = {}
St.__index = St

function S.new(cfg, deps)
  local s = setmetatable({
    cfg = cfg,
    call = deps.call,
    now = deps.now,
    log = deps.log or function() end,
    json = deps.json,
    list = deps.list,
    read_file = deps.read,
    write_file = deps.write,
    phase = "idle",
    plan = nil,
    action = nil, -- the crop_start command's id
    started = {}, -- [step] = true once that step's first run was loaded
    run = nil, -- { state = "loaded" | "running", step = k, t = when }
    runs = 0,
    ok = 0,
    fail = 0,
    note = nil,
    doing = nil,
    stock = {},
    next_at = 0,
    allowed = {}, -- addr -> work allowed when the station first switched it
  }, St)
  s:load()
  return s
end

function St:say(msg, level)
  msg = clip(msg, MAX_NOTE)
  if msg ~= self.note then
    self.note = msg
    self.log(level or "info", "crops: " .. msg)
  end
end

-- Saved after every change of phase, run or tallies, so a restart knows a run was under way.
function St:save()
  local path = self.cfg.state_path
  if not path or not self.write_file then
    return
  end
  local started = {}
  for k in pairs(self.started) do
    started[#started + 1] = k
  end
  table.sort(started)
  local doc = {
    v = 1,
    phase = self.phase,
    plan = self.plan and self.plan.raw or nil,
    action = self.action,
    started = started,
    runs = self.runs,
    ok = self.ok,
    fail = self.fail,
    run = self.run and { state = self.run.state, step = self.run.step } or nil,
  }
  local ok, err = self.write_file(path, self.json.encode(doc))
  if not ok then
    self.log("warn", "crops: can't save " .. path .. ": " .. tostring(err))
  end
end

function St:load()
  local path = self.cfg.state_path
  if not path or not self.read_file then
    return
  end
  local text = self.read_file(path)
  local doc = text and self.json.decode(text)
  if type(doc) ~= "table" or not LIVE[doc.phase] or type(doc.plan) ~= "table" then
    return
  end
  local plan = S.check_plan(doc.plan)
  if not plan then
    return
  end
  plan.raw = doc.plan
  self.plan = plan
  self.action = int(doc.action)
  self.started = {}
  if type(doc.started) == "table" then
    for _, k in ipairs(doc.started) do
      if int(k) then
        self.started[k] = true
      end
    end
  end
  self.runs, self.ok, self.fail = int(doc.runs) or 0, int(doc.ok) or 0, int(doc.fail) or 0
  if type(doc.run) == "table" and (doc.run.state == "loaded" or doc.run.state == "running") and int(doc.run.step)
    and plan.steps[doc.run.step] then
    self.run = { state = doc.run.state, step = doc.run.step, t = self.now() }
  end
  self.phase = "interrupted"
  self:say("the agent restarted during a run: a confirmed start resumes it, a confirmed stop winds it down", "warn")
end

-- The transposer and the machines' adapters (gt_machine, by GT's meta name). Direct calls only.
function St:find()
  local tp
  for addr in pairs(self.list("transposer")) do
    if not self.cfg.transposer or addr:sub(1, #self.cfg.transposer) == self.cfg.transposer then
      if tp then
        return nil, "more than one transposer; set crops.transposer to the station's address prefix"
      end
      tp = addr
    end
  end
  if not tp then
    return nil, "no transposer" .. (self.cfg.transposer and (" " .. self.cfg.transposer) or "")
  end
  local breeder
  local gens = {}
  for addr in pairs(self.list("gt_machine")) do
    local ok, name = self.call(addr, "getName")
    if ok and type(name) == "string" then
      if name:match(BREEDER_NAME) then
        if breeder then
          return nil, "more than one Crop Breeder on this computer's network; GOS won't guess which"
        end
        breeder = addr
      elseif name:match(GENERATOR_NAME) then
        gens[#gens + 1] = addr
      end
    end
  end
  if not breeder then
    return nil, "no Crop Breeder (an adapter next to it: basicmachine.cropbreeder.tier.NN)"
  end
  local copiers = {}
  for i, g in ipairs(self.cfg.generators) do
    local found
    for _, addr in ipairs(gens) do
      if (g.adapter == nil and #gens == 1) or (g.adapter and addr:sub(1, #g.adapter) == g.adapter) then
        found = addr
      end
    end
    if not found then
      return nil, ("no Seed Generator adapter for crops.generators[%d]%s"):format(i,
        g.adapter and (" (" .. g.adapter .. ")") or (#gens > 1 and ": several found, name its adapter" or ""))
    end
    copiers[i] = { side = g.side, addr = found }
  end
  self.tp, self.breeder, self.copiers = tp, breeder, copiers
  return true
end

-- One inventory as a list of slots (1-based): { name, damage, label, size } or false.
-- getAllStacks and its getAll are a server tick each.
function St:read(sd)
  local ok, value = self.call(self.tp, "getAllStacks", sd)
  if not ok or (type(value) ~= "table" and type(value) ~= "userdata") then
    return nil, "getAllStacks(" .. sd .. "): " .. tostring(value)
  end
  local ok2, all = pcall(function()
    return value.getAll()
  end)
  if not ok2 or type(all) ~= "table" then
    return nil, "getAll: " .. tostring(all)
  end
  local slots, n = {}, 0
  for i, st in pairs(all) do
    local idx = tonumber(i)
    if idx then
      idx = math.floor(idx) + 1 -- getAll is 0-based (OC's ItemStackArrayValue)
      if idx > n then
        n = idx
      end
      if type(st) == "table" and type(st.name) == "string" and int(st.size) and st.size > 0 then
        slots[idx] = { name = st.name, damage = int(st.damage) or 0, label = st.label, size = st.size }
      else
        slots[idx] = false
      end
    end
  end
  slots.n = n
  return slots
end

-- Move up to n items from slot `from` on one side to slot `to` on another; returns what moved. A
-- transferItem is one server tick and atomic: the items are in one place or the other.
function St:move(src, dst, n, from, to)
  local ok, moved, err = self.call(self.tp, "transferItem", src, dst, n, from, to)
  if not ok then
    return 0, tostring(moved)
  end
  if moved == nil then
    return 0, tostring(err)
  end
  return int(moved) or math.floor(tonumber(moved) or 0)
end

function St:observe()
  local chest, err = self:read(self.cfg.storage)
  if not chest then
    return nil, "storage chest: " .. err
  end
  local br, berr = self:read(self.cfg.breeder)
  if not br then
    return nil, "Crop Breeder: " .. berr
  end
  br.at = layout(br)
  if br.at.nin ~= 3 and br.at.nin ~= 6 then
    return nil, ("the breeder side (%d) has %d slots, not a Crop Breeder's 10 or 13"):format(self.cfg.breeder, br.n)
  end
  local gens = {}
  for i, c in ipairs(self.copiers) do
    local g, gerr = self:read(c.side)
    if not g then
      return nil, "Seed Generator " .. i .. ": " .. gerr
    end
    g.at = layout(g)
    if g.at.nin ~= 2 then
      return nil, ("the generator side (%d) has %d slots, not a Seed Generator's 9"):format(c.side, g.n)
    end
    g.holds = nil
    for j = g.at.first, g.at.last do
      if g[j] and g[j].name == SEED then
        g.holds = g[j].label -- the parked seed: this copier's crop
      end
    end
    gens[i] = g
  end
  return { chest = chest, br = br, gens = gens }
end

function St:work(addr, allowed)
  if self.allowed[addr] == nil then
    local ok, was = self.call(addr, "isWorkAllowed")
    self.allowed[addr] = not ok or was ~= false
  end
  local ok, err = self.call(addr, "setWorkAllowed", allowed)
  if not ok then
    return nil, "setWorkAllowed: " .. tostring(err)
  end
  return true
end

function St:busy(addr)
  local ok, work = self.call(addr, "hasWork")
  if not ok then
    return nil, tostring(work)
  end
  return work == true
end

-- Put the breeder's work switch back as the station found it. The copiers stay paused: one
-- left running would copy its parked seed until its output slot holds 64.
function St:restore()
  local was = self.allowed[self.breeder]
  if was ~= nil then
    self.call(self.breeder, "setWorkAllowed", was)
    self.allowed[self.breeder] = nil
  end
end

function St:labels()
  local out, seen = {}, {}
  local function add(l)
    if l and not seen[l] then
      seen[l] = true
      out[#out + 1] = l
    end
  end
  if self.plan then
    for _, s in ipairs(self.plan.steps) do
      for _, p in ipairs(s.parents) do
        add(p)
      end
      add(s.child)
    end
  end
  return out
end

function St:set(phase)
  if phase ~= self.phase then
    self.phase = phase
    self:save()
  end
end

---------------------------------------------------------------------------------------------
-- Commands

function St:start(id, args)
  local plan, err = S.check_plan(args)
  if not plan then
    return { ok = false, state = "refused", err = err }
  end
  plan.raw = args
  if LIVE[self.phase] then
    if self.plan and self.plan.id == plan.id and self.phase == "running" then
      return { ok = true, state = "done" } -- already running this plan
    end
    return { ok = false, state = "refused", err = "the station is " .. self.phase .. " another plan; stop it first" }
  end
  local resume = self.phase == "interrupted" and self.plan and self.plan.id == plan.id
  if self.phase == "interrupted" and not resume then
    return { ok = false, state = "refused", err = "a run of another plan was interrupted; stop it first" }
  end
  local found, ferr = self:find()
  if not found then
    return { ok = false, state = "failed", err = ferr }
  end
  if not resume then
    self.started, self.runs, self.ok, self.fail, self.run = {}, 0, 0, 0, nil
  end
  self.plan, self.action, self.note, self.doing = plan, id, nil, nil
  self.finishing = nil
  self:set("running")
  self:say((resume and "resumed: " or "started: ") .. plan.target .. " in " .. #plan.steps .. " steps, stocking " .. plan.count)
  self.next_at = 0
  return { ok = true, state = "done" }
end

function St:stop(id)
  if self.phase == "stopping" and not self.finishing then
    return { ok = true, state = "done" }
  end
  if self.phase ~= "running" and self.phase ~= "interrupted" and self.phase ~= "stopping" then
    return { ok = true, state = "done" } -- nothing runs
  end
  if not self.tp then
    local found, ferr = self:find()
    if not found then
      return { ok = false, state = "failed", err = ferr }
    end
  end
  self.stop_id, self.finishing = id, nil
  self:set("stopping")
  self.doing = "stopping"
  self:say("stopping: no new work; collecting the station's items")
  self.next_at = 0
  return { ok = true, state = "done" }
end

---------------------------------------------------------------------------------------------
-- One step

-- Outputs are collected every step: the copiers' copies and the breeder's child.
function St:harvest(o)
  local moved = false
  for i, g in ipairs(o.gens) do
    local gs = g[g.at.out]
    if gs then
      moved = self:move(self.copiers[i].side, self.cfg.storage, gs.size, g.at.out) > 0 or moved
    end
  end
  local bs = o.br[o.br.at.out]
  if bs then
    if self:move(self.cfg.breeder, self.cfg.storage, bs.size, o.br.at.out) > 0 then
      moved = true
      if self.run and is_seed(bs, self.plan.steps[self.run.step].child) then
        self.run.got = true
      end
    end
  end
  return moved
end

function St:copier_of(o, label)
  for i, g in ipairs(o.gens) do
    if g.holds == label then
      return i, g
    end
  end
  -- An empty generator takes a crop of the plan's copy list, in its order: the first ranks that
  -- have no copier yet get one, so a later crop never takes the generator an earlier one needs.
  local free = {}
  for i, g in ipairs(o.gens) do
    if not g.holds and inputs_empty(g) then
      free[#free + 1] = i
    end
  end
  local n = 0
  for _, l in ipairs(self.plan.copy) do
    local held = false
    for _, g in ipairs(o.gens) do
      if g.holds == l then
        held = true
      end
    end
    if not held then
      n = n + 1
      if n > #free then
        return nil
      end
      if l == label then
        return free[n], o.gens[free[n]]
      end
    end
  end
  return nil
end

-- How many of `label` the chest should hold before step j's next run: the plan's copies before
-- the step's first run when a copier makes them, else keep + 1.
function St:wanted(o, label, j)
  local base = self.cfg.keep + 1
  if not self.started[j] and self:copier_of(o, label) then
    return math.max(base, self.plan.steps[j].copies)
  end
  return base
end

-- What to do next so the chest holds `amount` of `label`: nil when it does, else an action
-- { kind = "copy" | "park" | "load" | "wait", ... }.
function St:resolve(o, label, amount, depth)
  local have = count(o.chest, label)
  if have >= amount then
    return nil
  end
  if depth > MAX_STEPS + 1 then
    return { kind = "wait", msg = "the plan loops on " .. label }
  end
  local gi, g = self:copier_of(o, label)
  if g and g.holds == label then
    return { kind = "copy", gen = gi, label = label, have = have, want = amount }
  end
  if g and have >= 1 then
    return { kind = "park", gen = gi, label = label }
  end
  local j = self.plan.by_child[label]
  if j then
    local s = self.plan.steps[j]
    for _, p in ipairs(s.parents) do
      local a = self:resolve(o, p, self:wanted(o, p, j), depth + 1)
      if a then
        return a
      end
    end
    for _, c in ipairs(s.cat) do
      if count_item(o.chest, c.name, c.damage) < c.n then
        return { kind = "wait", msg = ("step %d needs %d %s:%d in the chest (the recipe's catalyst)"):format(j, c.n, c.name, c.damage) }
      end
    end
    return { kind = "load", step = j }
  end
  return { kind = "wait", msg = ("needs %d more %s in the chest (analysed)"):format(amount - have, label) }
end

-- A copier's duplication catalyst: kept in its second input slot.
function St:feed(o, gi, label)
  local g = o.gens[gi]
  for _, c in ipairs(self.plan.copy_cat[label] or {}) do
    local inside = count_item(g, c.name, c.damage)
    if inside < c.n then
      local slot = find_slot(o.chest, function(st) return st.name == c.name and st.damage == c.damage end)
      if not slot then
        return nil, ("copying %s needs %d %s:%d (its duplication catalyst) in the chest"):format(label, c.n, c.name, c.damage)
      end
      self:move(self.cfg.storage, self.copiers[gi].side, math.min(64, o.chest[slot].size), slot, g.at.last)
    end
  end
  return true
end

function St:pause_copiers(except)
  for i, c in ipairs(self.copiers) do
    if i ~= except and self.allowed[c.addr] ~= nil then
      self:work(c.addr, false)
    end
  end
end

function St:act(o, a)
  if a.kind == "copy" then
    local ok, err = self:feed(o, a.gen, a.label)
    if not ok then
      self:say(err, "warn")
      return
    end
    self:work(self.copiers[a.gen].addr, true)
    self:pause_copiers(a.gen)
    self.doing = ("copying %s (%d of %d)"):format(a.label, a.have, a.want)
  elseif a.kind == "park" then
    local g = o.gens[a.gen]
    local slot = find_slot(o.chest, function(st) return is_seed(st, a.label) end)
    self:work(self.copiers[a.gen].addr, false)
    local n, err = self:move(self.cfg.storage, self.copiers[a.gen].side, 1, slot, g.at.first)
    if n < 1 then
      self:say("couldn't give the Seed Generator a " .. a.label .. ": " .. tostring(err), "warn")
      return
    end
    self.doing = "Seed Generator " .. a.gen .. " now copies " .. a.label
    self:say(("Seed Generator %d keeps one %s to copy it (GT lets no automation take it back out)"):format(a.gen, a.label))
  elseif a.kind == "load" then
    self:pause_copiers()
    self:load_breeder(o, a.step)
  else
    self:pause_copiers()
    self.doing = "waiting"
    self:say(a.msg, "warn")
  end
end

function St:load_breeder(o, j)
  local s = self.plan.steps[j]
  local bin = o.br.at
  if #s.parents + #s.cat > bin.nin then
    self:set("failed")
    self:say(("step %d needs %d input slots; this Crop Breeder has %d"):format(j, #s.parents + #s.cat, bin.nin), "error")
    return
  end
  if not inputs_empty(o.br) or o.br[bin.out] or self:busy(self.breeder) ~= false then
    self.doing = "waiting for the Crop Breeder to be idle and empty"
    return
  end
  for _, p in ipairs(s.parents) do
    -- never the last seed: loading one must leave `keep` in the chest
    if count(o.chest, p) < self.cfg.keep + 1 then
      return
    end
  end
  local ok, level = self.call(self.tp, "getTankLevel", self.cfg.breeder)
  if not ok or type(level) ~= "number" then
    self:say("can't read the Crop Breeder's fertilizer: " .. tostring(level), "warn")
    return
  end
  if level < s.fert then
    self.doing = "waiting for enriched fertilizer"
    self:say(("step %d: the Crop Breeder has %d mB of enriched fertilizer; a run takes %d"):format(j, level, s.fert), "warn")
    return
  end
  local wok, werr = self:work(self.breeder, false) -- no recipe check on a half-loaded set
  if not wok then
    self:say("Crop Breeder: " .. werr, "warn")
    return
  end
  local slot, short = bin.first, nil
  for _, c in ipairs(s.cat) do
    local cslot = find_slot(o.chest, function(st) return st.name == c.name and st.damage == c.damage end)
    local n, merr = self:move(self.cfg.storage, self.cfg.breeder, c.n, cslot, slot)
    if n < c.n then
      short = short or ("catalyst %s:%d: %s"):format(c.name, c.damage, merr or ("moved " .. n))
    end
    slot = slot + 1
  end
  for _, p in ipairs(s.parents) do
    if not short then
      local pslot = find_slot(o.chest, function(st) return is_seed(st, p) end)
      local n, merr = self:move(self.cfg.storage, self.cfg.breeder, 1, pslot, slot)
      if n < 1 then
        short = p .. ": " .. tostring(merr or "not moved")
      end
      slot = slot + 1
    end
  end
  self:work(self.breeder, true)
  self.started[j] = true
  self.run = { state = "loaded", step = j, t = self.now() }
  if short then
    self:say(("step %d: a move into the Crop Breeder failed (%s); what went in stays until a run takes it"):format(j, short), "warn")
  end
  self.doing = ("step %d: loaded the Crop Breeder"):format(j)
  self:save()
end

function St:finish_run()
  local r = self.run
  local s = self.plan.steps[r.step]
  self.runs = self.runs + 1
  self.run = nil
  if r.got then
    self.ok = self.ok + 1
    self:say(("step %d: bred %s (run %d)"):format(r.step, s.child, self.runs))
  else
    self.fail = self.fail + 1
    self:say(("step %d: the run failed (the Crop Breeder used up its parents); the station tries again"):format(r.step))
  end
  self:save()
end

-- A run under way: loaded until the breeder takes its inputs, then running until it has no work.
-- `busy` is hasWork read BEFORE `o` was observed, so a run that ends in between is seen next
-- time with its child, never as a failure. Returns true while the run isn't over.
function St:watch_run(o, busy)
  local r = self.run
  if not r then
    return false
  end
  if r.state == "loaded" then
    local s = self.plan.steps[r.step]
    local still = 0
    for i = o.br.at.first, o.br.at.last do
      for _, p in ipairs(s.parents) do
        if is_seed(o.br[i], p) then
          still = still + 1
        end
      end
    end
    if still > 0 then
      if self.now() - r.t > self.cfg.start_s then
        self:say(("step %d: the Crop Breeder hasn't started in %d s (power? fertilizer?); its inputs stay there, GT lets no automation take them back")
          :format(r.step, self.cfg.start_s), "warn")
      end
      return true
    end
    r.state, r.t = "running", self.now()
    self:save()
  end
  if busy == nil then
    self:say("Crop Breeder: can't read hasWork", "warn")
    return true
  end
  if busy then
    self.doing = ("step %d: breeding %s"):format(r.step, self.plan.steps[r.step].child)
    if self.now() - r.t > self.cfg.run_max_s then
      self:say(("step %d: the run has taken over %d s; still waiting"):format(r.step, self.cfg.run_max_s), "warn")
    end
    return true
  end
  if not r.got and is_seed(o.br[o.br.at.out], self.plan.steps[r.step].child) then
    return true -- the child is there: the next harvest takes it
  end
  self:finish_run()
  return false
end

-- Stop or finish: no new work; wait for the breeder's run and the copiers, collect their outputs.
function St:wind_down(o, busy)
  self:pause_copiers()
  local r = self.run
  if r and r.state == "loaded" and self.now() - r.t > self.cfg.start_s then
    self.run = nil -- it never started; its inputs can only wait there (see the note below)
  elseif self:watch_run(o, busy) then
    self.doing = "waiting for the Crop Breeder's run to end"
    return
  end
  local idle = self:busy(self.breeder)
  local again = self:observe()
  if not again or again.br[again.br.at.out] or idle ~= false then
    self.doing = "collecting the station's items"
    return
  end
  for i, g in ipairs(again.gens) do
    local c = self.copiers[i]
    if g[g.at.out] or (self.allowed[c.addr] ~= nil and self:busy(c.addr) ~= false) then
      self.doing = "collecting the Seed Generators' last copies"
      return
    end
  end
  local left = inputs_empty(again.br) and "" or "; items wait in the Crop Breeder's inputs (it never started): take them out by hand"
  self:restore()
  if self.finishing then
    self.finishing = nil
    self:set("done")
    self:say(("done: %d %s in the chest (%d runs, %d failed)%s"):format(self.plan.count, self.plan.target, self.runs, self.fail, left))
  else
    self:set("stopped")
    self:say("stopped; nothing of the station's is left in transit" .. left)
  end
  self.doing = nil
  self:count_stock(again)
end

function St:count_stock(o)
  local stock = {}
  for _, l in ipairs(self:labels()) do
    stock[l] = count(o.chest, l)
  end
  self.stock = stock
  local parked = {}
  for i, g in ipairs(o.gens) do
    parked[i] = g.holds or false
  end
  self.parked = parked
end

function St:step()
  self.next_at = self.now() + self.cfg.step_s
  if not LIVE[self.phase] then
    return
  end
  if not self.tp then
    local found, ferr = self:find()
    if not found then
      self:say(ferr, "warn")
      return
    end
  end
  local busy = self:busy(self.breeder) -- before the look (watch_run)
  local o, err = self:observe()
  if not o then
    self:say(err, "warn")
    return
  end
  if self:harvest(o) then
    o, err = self:observe()
    if not o then
      self:say(err, "warn")
      return
    end
  end
  self:count_stock(o)
  if self.phase == "stopping" then
    return self:wind_down(o, busy)
  end
  local breeding = self:watch_run(o, busy)
  local a = self:resolve(o, self.plan.target, self.plan.count, 0)
  if not a then
    self.finishing = true
    self:set("stopping")
    self.doing = "finishing"
    self.next_at = 0
    return
  end
  if a.kind == "load" and (breeding or self.run) then
    return -- the breeder is busy; the next look decides again
  end
  self:act(o, a)
end

---------------------------------------------------------------------------------------------
-- Status (the telemetry's `crops`)

function St:doc()
  local d = {
    state = self.phase,
    runs = self.runs,
    ok = self.ok,
    fail = self.fail,
  }
  if self.plan then
    d.plan = self.plan.id
    d.action = self.action
    d.target = self.plan.target
    d.count = self.plan.count
    d.steps = #self.plan.steps
    if self.run then
      d.step = self.run.step
    end
  end
  if self.doing then
    d.doing = clip(self.doing, MAX_NOTE)
  end
  if self.note then
    d.note = self.note
  end
  local stock, n = {}, 0
  for l, c in pairs(self.stock) do
    if n < 16 then
      stock[l] = c
      n = n + 1
    end
  end
  d.stock = stock
  if self.parked then
    d.copiers = self.parked
  end
  return d
end

return S
