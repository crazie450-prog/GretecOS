--[[ run.lua: the Lua 5.3 test runner for oc/*.lua (WO-7.2).

  lua5.3 tests/oc/run.lua [--dump DIR] [NAME ...]

Runs every test_*.lua suite here (or the suites/tests whose names contain a NAME) in a fresh fake
OpenComputers world (fakeoc.lua) each, with a yield watchdog. --dump DIR writes every message the
agent sent and every reply the fake core gave to DIR, for tests/oc/test_oc_agent.py to validate
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
  "test_runtime",
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

local passed, failed, failures = 0, 0, {}
for _, suite in ipairs(SUITES) do
  local tests = dofile(root .. "/tests/oc/" .. suite .. ".lua")
  for _, t in ipairs(tests) do
    local name, fn = t[1], t[2]
    if wanted(suite, name) then
      T.current = suite .. "." .. name
      -- A fresh world for every test; the watchdog watches its clock.
      local world_ref = {}
      T.on_world = function(w)
        world_ref.w = w
      end
      debug.sethook(function()
        if world_ref.w then
          world_ref.w:instruction_hook()
        end
      end, "", 100000)
      local ok, err = xpcall(fn, debug.traceback)
      debug.sethook()
      T.on_world = nil
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
