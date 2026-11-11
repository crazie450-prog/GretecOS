-- oc/json.lua: the encoder and decoder the agent uses for every message (WO-7.2).
local T = require("testlib")
local json = require("json")

local tests = {}
local function test(name, fn)
  tests[#tests + 1] = { name, fn }
end

test("scalars", function()
  T.eq(json.encode(true), "true")
  T.eq(json.encode(false), "false")
  T.eq(json.encode(json.null), "null")
  T.eq(json.encode(0), "0")
  T.eq(json.encode(-17), "-17")
  T.eq(json.encode(math.maxinteger), "9223372036854775807")
  T.eq(json.encode(math.mininteger), "-9223372036854775808")
  T.eq(json.encode(1.5), "1.5")
  T.eq(json.encode(0.1), "0.1")
  T.eq(json.encode(61.25), "61.25", "14 significant digits when they round-trip")
  T.eq(json.encode(1 / 3), "0.33333333333333331", "17 when 14 would not")
  T.eq(json.decode(json.encode(1 / 3)), 1 / 3, "exact round trip")
  T.eq(json.decode(json.encode(2 ^ 0.5)), 2 ^ 0.5, "exact round trip")
  T.eq(json.encode(0 / 0), "null", "NaN")
  T.eq(json.encode(math.huge), "null", "inf")
  T.eq(json.encode(-math.huge), "null", "-inf")
end)

test("strings are ASCII-only with escapes", function()
  T.eq(json.encode("plain"), '"plain"')
  T.eq(json.encode('a"b\\c/d'), '"a\\"b\\\\c/d"')
  T.eq(json.encode("\n\r\t\b\f"), '"\\n\\r\\t\\b\\f"')
  T.eq(json.encode("\0\1\31\127"), '"\\u0000\\u0001\\u001f\\u007f"')
  T.eq(json.encode("caf\195\169"), '"caf\\u00e9"', "two-byte UTF-8")
  T.eq(json.encode("\226\130\172"), '"\\u20ac"', "three-byte UTF-8")
  T.eq(json.encode("\240\159\152\128"), '"\\ud83d\\ude00"', "four-byte UTF-8 as a surrogate pair")
  T.eq(json.encode("\194\167c5"), '"\\u00a7c5"', "a section-sign colour code")
  local enc = json.encode("all \194\167 bytes \240\159\152\128 ascii")
  T.no(enc:find("[\128-\255]"), "no byte above 0x7f in the output")
end)

test("invalid UTF-8 becomes U+FFFD", function()
  T.eq(json.encode("a\255b"), '"a\\ufffdb"', "a stray byte")
  T.eq(json.encode("\192\128"), '"\\ufffd\\ufffd"', "an overlong NUL")
  T.eq(json.encode("\237\160\128"), '"\\ufffd\\ufffd\\ufffd"', "an encoded surrogate")
  T.eq(json.encode("\244\144\128\128"), '"\\ufffd\\ufffd\\ufffd\\ufffd"', "beyond U+10FFFF")
  T.eq(json.encode("\226\130"), '"\\ufffd\\ufffd"', "cut short")
end)

test("arrays and objects", function()
  T.eq(json.encode({ 1, 2, 3 }), "[1,2,3]")
  T.eq(json.encode(json.array()), "[]", "a marked empty array")
  T.eq(json.encode(json.object()), "{}", "a marked empty object")
  T.eq(json.encode({}), "{}", "an unmarked empty table is an object")
  T.eq(json.encode({ b = 1, a = 2, c = { 3 } }), '{"a":2,"b":1,"c":[3]}', "sorted keys")
  T.eq(json.encode(json.array({ "x", "y" })), '["x","y"]', "a marked array")
  T.raises(function()
    json.encode(json.object({ "x" }))
  end, "object key is a number", "a marked object is never an array")
  T.raises(function()
    json.encode({ [1] = "a", [3] = "c" })
  end, "object key is a number", "a table with holes is not an array")
  T.raises(function()
    json.encode({ f = print })
  end, "cannot encode a function")
  local deep = {}
  local cur = deep
  for _ = 1, 40 do
    cur.x = {}
    cur = cur.x
  end
  T.raises(function()
    json.encode(deep)
  end, "nesting deeper than 32")
  local cycle = {}
  cycle.self = cycle
  T.raises(function()
    json.encode(cycle)
  end, "nesting deeper than 32", "a cycle")
end)

test("decode round trip", function()
  local text = '{"a":[1,2.5,-300,"x",true,false,null],"b":{},"c":[],"d":"\\u00e9\\ud83d\\ude00\\n\\/"}'
  local v = assert(json.decode(text))
  T.eq(v.a[1], 1)
  T.eq(math.type(v.a[1]), "integer")
  T.eq(v.a[2], 2.5)
  T.eq(math.type(v.a[3]), "integer")
  T.eq(v.a[5], true)
  T.eq(v.a[6], false)
  T.eq(v.a[7], json.null, "null keeps its place in an array")
  T.ok(json.is_object(v.b))
  T.ok(json.is_array(v.c))
  T.eq(v.d, "\195\169\240\159\152\128\n/")
  T.eq(json.encode(v), '{"a":[1,2.5,-300,"x",true,false,null],"b":{},"c":[],"d":"\\u00e9\\ud83d\\ude00\\n/"}')
end)

test("decode numbers", function()
  T.eq(json.decode("9223372036854775807"), math.maxinteger)
  T.eq(math.type(json.decode("9223372036854775808")), "float", "too big for 64 bits")
  T.eq(json.decode("-0"), 0)
  T.eq(json.decode("1e3"), 1000.0)
  T.eq(math.type(json.decode("1e3")), "float")
  T.eq(json.decode("1.25E-2"), 0.0125)
  for _, bad in ipairs({ "01", "+1", ".5", "1.", "1e", "-", "0x10", "1e+", "--1", "Infinity", "NaN" }) do
    local v, err = json.decode(bad)
    T.eq(v, nil, "decode " .. bad)
    T.ok(err, "an error for " .. bad)
  end
end)

test("decode is strict and never raises", function()
  local bad = {
    "",
    " ",
    "[1,]",
    "{\"a\":1,}",
    "{a:1}",
    "{\"a\" 1}",
    "[1 2]",
    "\"abc",
    "\"a\\x\"",
    "\"a\\u12\"",
    "\"tab\tin string\"",
    "{\"a\":1} x",
    "[true] [false]",
    "nul",
    "tru",
    "// comment\n1",
    string.rep("[", 33) .. string.rep("]", 33),
  }
  for _, text in ipairs(bad) do
    local ok, v, err = pcall(json.decode, text)
    T.ok(ok, "decode raised for " .. string.format("%q", text))
    T.eq(v, nil, "decoded " .. string.format("%q", text))
    T.has(err, "json:", "error text for " .. string.format("%q", text))
  end
  local v, err = json.decode(42)
  T.eq(v, nil)
  T.has(err, "expected a string")
  T.ok(json.decode(string.rep("[", 32) .. string.rep("]", 32)), "32 levels are fine")
end)

test("surrogates in escapes", function()
  T.eq(json.decode('"\\ud83d\\ude00"'), "\240\159\152\128")
  T.eq(json.decode('"\\ud83d"'), "\239\191\189", "a lone high surrogate becomes U+FFFD")
  T.eq(json.decode('"\\ude00"'), "\239\191\189", "a lone low surrogate becomes U+FFFD")
  T.eq(json.decode('"\\ud83dx"'), "\239\191\189x")
end)

test("duplicate keys: the last wins", function()
  T.eq(json.decode('{"a":1,"a":2}').a, 2)
end)

test("a big document stays quick", function()
  local t = json.array()
  for i = 1, 500 do
    t[i] = { addr = string.format("%08x-0000-4000-8000-%012x", i, i), sensor = json.array({ "Problems: \194\167c0", "line " .. i }) }
  end
  local s = json.encode(t)
  local back = assert(json.decode(s))
  T.eq(#back, 500)
  T.eq(back[500].sensor[1], "Problems: \194\167c0")
end)

return tests
