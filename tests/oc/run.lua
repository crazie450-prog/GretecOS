--[[ run.lua: the Lua 5.3 test runner for oc/*.lua (WO-7.2).

  lua5.3 tests/oc/run.lua [--dump DIR] [NAME ...]

Runs every test_*.lua suite here (or the suites/tests whose names contain a NAME) in a fresh fake
OpenComputers world (fakeoc.lua) each, with a yield watchdog. --dump DIR writes every message the
agent sent (as it sends it, so a long lean run holds none of them) and every reply the standard
fake core built to DIR, one JSON document a line, for tests/oc/test_oc_agent.py to validate
against docs/contracts/oc_bridge.schema.json. Exit status 0 when every test passed.
]]

local script = arg and arg[0] or "tests/oc/run.lua"
local root = script:match("^(.*)/tests/oc/run%.lua$") or "."
package.path = root .. "/tests/oc/?.lua;" .. root .. "/oc/?.lua;" .. package.path

local F = require("fakeoc")
F.set_root(root)
local T = require("testlib")

local SUITES = {
  "test_json",
  "test_config",
  "test_discovery",
  "test_cadence",
  "test_payload",
  "test_commands",
  "test_transport",
  "test_mailbox",
  "test_display",
  "test_runtime",
  "test_station",
}

local filters = {}
local i = 1
while arg and arg[i] do
  if arg[i] == "--dump" then
    T.dump_dir = assert(arg[i + 1], "--dump needs a folder")
    i = i + 2
  else
    filters[#filters + 1] = arg[i]
    i = i + 1
  end
end

local function wanted(suite, name)
  if #filters == 0 then
    return true
  end
  for _, f in ipairs(filters) do
    if suite:find(f, 1, true) or name:find(f, 1, true) then
      return true
    end
  end
  return false
end

-- <test>-w<n>-req.jsonl: every telemetry message world n of a test received, written as it is
-- sent; <test>-w<n>-reply.jsonl: the replies the standard fake core built there. For the schema
-- check in test_oc_agent.py.
local function dump_file(id, wi, kind)
  return assert(io.open(("%s/%s-w%d-%s.jsonl"):format(T.dump_dir, id, wi, kind), "wb"))
end

local function dump_replies(id, wi, w)
  if #(w.valid_replies or {}) > 0 then
    local f = dump_file(id, wi, "reply")
    for _, body in ipairs(w.valid_replies) do
      f:write(body, "\n")
    end
    f:close()
  end
end

local passed, failed, failures = 0, 0, {}
for _, suite in ipairs(SUITES) do
  local tests = dofile(root .. "/tests/oc/" .. suite .. ".lua")
  for _, t in ipairs(tests) do
    local name, fn = t[1], t[2]
    if wanted(suite, name) then
      T.current = suite .. "." .. name
      local id = T.current:gsub("[^%w_%-]", "_")
      -- A fresh world for every test; the watchdog watches the newest one's clock.
      local worlds, files = {}, {}
      T.on_world = function(w)
        worlds[#worlds + 1] = w
        local wi = #worlds
        if T.dump_dir then
          w.on_request = function(body)
            if type(body) == "string" then
              files[wi] = files[wi] or dump_file(id, wi, "req")
              files[wi]:write(body, "\n")
            end
          end
        end
      end
      debug.sethook(function()
        local w = worlds[#worlds]
        if w then
          w:instruction_hook()
        end
      end, "", 100000)
      local ok, err = xpcall(fn, debug.traceback)
      debug.sethook()
      T.on_world = nil
      for _, f in pairs(files) do
        f:close()
      end
      if T.dump_dir then
        for wi, w in ipairs(worlds) do
          dump_replies(id, wi, w)
        end
      end
      if ok then
        passed = passed + 1
        print("ok   " .. T.current)
      else
        failed = failed + 1
        failures[#failures + 1] = T.current .. "\n" .. tostring(err)
        print("FAIL " .. T.current)
      end
    end
  end
end

for _, f in ipairs(failures) do
  print("\n" .. f)
end
print(("\n%d passed, %d failed"):format(passed, failed))
os.exit(failed == 0 and passed > 0 and 0 or 1)
