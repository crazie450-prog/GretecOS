"""Java arithmetic, as GT's math runs it (WO-3.1, D173).

GT's overclock, parallel and processing-logic code is Java, and its numbers depend on Java's
rules, which Python's don't follow:

- ``long`` and ``int`` arithmetic wraps at 64 and 32 bits, and ``(int)`` of a ``long`` keeps the
  low 32 bits; integer division truncates toward zero;
- a cast from ``double`` truncates toward zero, maps NaN to 0 and saturates at the type's bounds
  (``(long) Math.ceil(x)`` is ``Long.MAX_VALUE`` when ``x`` overflows, which GT reads as "power
  overflow");
- ``Math.ceil``, ``Math.floor``, ``Math.log`` and ``/`` on doubles return a double for every
  input (Python's raise on infinities, NaN or 0), ``Math.max``/``Math.min`` return NaN when
  either side is NaN, and ``Math.round`` rounds half up to a ``long``;
- ``Math.log`` is computed here as the correctly rounded logarithm, not with the platform's C
  library, so the port gives the same numbers on every machine (D175);
- ``float`` steps round to IEEE single precision;
- GT's own ``GTUtility`` helpers (``powInt``, ``log4``, ``log4ceil``, ``safeInt``, ``clamp``,
  ``ceilDiv``, ``addSafe``, ``mulSafe``) compute in their own ways; ``powInt`` multiplies by
  repeated squaring, which rounds differently from ``math.pow``.

Each helper names the Java operation it stands for. Written from the Java Language
Specification and from what GT 5.09.54.265's ``GTUtility`` does (GT is LGPL-3.0: its source was
read to learn the behaviour, nothing was copied).
"""

from __future__ import annotations

import decimal
import functools
import math
import struct

INT_MIN = -(2**31)
INT_MAX = 2**31 - 1
LONG_MIN = -(2**63)
LONG_MAX = 2**63 - 1
_TWO_31 = 2.0**31
_TWO_63 = 2.0**63


def wrap_long(value: int) -> int:
    """A Java ``long`` result: ``value`` modulo 2^64, signed."""
    return (value + 2**63) % 2**64 - 2**63


def wrap_int(value: int) -> int:
    """A Java ``int`` result, and ``(int)`` of a ``long``: the low 32 bits, signed."""
    return (value + 2**31) % 2**32 - 2**31


def lmul(a: int, b: int) -> int:
    """``a * b`` on Java longs (wraps)."""
    return wrap_long(a * b)


def ldiv(a: int, b: int) -> int:
    """``a / b`` on Java longs: truncated toward zero (``Long.MIN_VALUE / -1`` wraps)."""
    if b == 0:
        raise ZeroDivisionError("long division by zero")
    q = abs(a) // abs(b)
    return wrap_long(q if (a < 0) == (b < 0) else -q)


def to_long(x: float) -> int:
    """``(long) x`` for a double: toward zero; NaN is 0; saturates at the long bounds."""
    if math.isnan(x):
        return 0
    if x >= _TWO_63:
        return LONG_MAX
    if x <= -_TWO_63:
        return LONG_MIN
    return int(x)


def to_int(x: float) -> int:
    """``(int) x`` for a double: toward zero; NaN is 0; saturates at the int bounds."""
    if math.isnan(x):
        return 0
    if x >= _TWO_31:
        return INT_MAX
    if x <= -_TWO_31:
        return INT_MIN
    return int(x)


def saturated_int(value: int) -> int:
    """Guava's ``Ints.saturatedCast(long)``: ``value`` clamped to the int range."""
    return max(INT_MIN, min(INT_MAX, value))


def to_float(x: float) -> float:
    """``(float) x``: ``x`` rounded to IEEE single precision (returned as a Python float)."""
    try:
        return struct.unpack("<f", struct.pack("<f", x))[0]
    except OverflowError:
        return math.copysign(math.inf, x)


def ceil(x: float) -> float:
    """``Math.ceil``: a double; infinities and NaN pass through, -0.5 gives -0.0."""
    if not math.isfinite(x):
        return x
    r = float(math.ceil(x))
    return math.copysign(0.0, x) if r == 0 else r


def floor(x: float) -> float:
    """``Math.floor``: a double; infinities and NaN pass through, -0.0 stays -0.0."""
    if not math.isfinite(x):
        return x
    r = float(math.floor(x))
    return math.copysign(0.0, x) if r == 0 else r


def round_long(x: float) -> int:
    """``Math.round(double)``: the nearest long, ties toward positive infinity; NaN is 0;
    saturates at the long bounds."""
    if math.isnan(x):
        return 0
    if x >= _TWO_63:
        return LONG_MAX
    if x <= -_TWO_63:
        return LONG_MIN
    f = math.floor(x)
    return int(f) + (1 if x - f >= 0.5 else 0)


def ddiv(a: float, b: float) -> float:
    """``a / b`` on doubles (IEEE 754): dividing by zero gives an infinity or NaN, never an
    error."""
    try:
        return a / b
    except ZeroDivisionError:
        if math.isnan(a) or a == 0:
            return math.nan
        return math.copysign(math.inf, a) * math.copysign(1.0, b)


def dmax(a: float, b: float) -> float:
    """``Math.max`` on doubles: NaN if either is NaN; 0.0 is above -0.0."""
    if math.isnan(a) or math.isnan(b):
        return math.nan
    if a == b == 0.0:
        return b if math.copysign(1.0, a) < 0 else a
    return max(a, b)


def dmin(a: float, b: float) -> float:
    """``Math.min`` on doubles: NaN if either is NaN; -0.0 is below 0.0."""
    if math.isnan(a) or math.isnan(b):
        return math.nan
    if a == b == 0.0:
        return a if math.copysign(1.0, a) < 0 else b
    return min(a, b)


def log(x: float) -> float:
    """``Math.log``: the natural logarithm; -Infinity at 0, NaN below 0 and at NaN.

    The correctly rounded one. Java allows ``Math.log`` an error of one unit in the last place
    and C libraries take it (glibc's differs from Java's on a few inputs in 100 000), so
    ``math.log`` would make GT's numbers depend on the machine. The JVM's own ``Math.log`` on
    x86-64 gave the correctly rounded value on every one of 146 000 inputs tried (D175)."""
    if math.isnan(x) or x < 0:
        return math.nan
    if x == 0:
        return -math.inf
    if x == math.inf:
        return math.inf
    return _ln(x)


@functools.lru_cache(maxsize=4096)
def _ln(x: float) -> float:
    # decimal's ln is correctly rounded at its precision; 40 digits then round once more to
    # the nearest double.
    return float(decimal.Decimal(x).ln(decimal.Context(prec=40)))


def _bits_to_double(bits: int) -> float:
    """``Double.longBitsToDouble``."""
    return struct.unpack("<d", struct.pack("<q", wrap_long(bits)))[0]


def _pow_by_squaring(base: float, exp: int) -> float:
    # GT builds powers of 2 and 4 straight from the exponent bits, and squares otherwise. A
    # negative exponent only gets here when negating Integer.MIN_VALUE overflowed; the bit
    # trick and the loop then do what Java does with it.
    if base == 2:
        return math.inf if exp > 1023 else _bits_to_double((exp + 1023) << 52)
    if base == 4:
        return math.inf if exp > 511 else _bits_to_double((exp * 2 + 1023) << 52)
    result = 1.0
    while exp > 0:
        if exp & 1:
            result *= base
        base *= base
        exp >>= 1
    return result


def pow_int(base: float, exp: int) -> float:
    """``GTUtility.powInt(double, int)``: ``base`` to an int power by repeated squaring (exact
    for bases 2 and 4), ``1 / base^-exp`` for negative powers."""
    if exp > 0:
        return _pow_by_squaring(base, exp)
    if exp < 0:
        return ddiv(1.0, _pow_by_squaring(base, wrap_int(-exp)))
    return 1.0


def log4(a: int) -> int:
    """``GTUtility.log4(long)``: floor(log4(a)), 0 for ``a <= 1``."""
    if a <= 1:
        return 0
    return (a.bit_length() - 1) >> 1


def log4ceil(a: int) -> int:
    """``GTUtility.log4ceil(long)``: ceil(log4(a)), 0 for ``a <= 1``."""
    if a <= 1:
        return 0
    return ((a - 1).bit_length() + 1) >> 1


def safe_int(number: int, margin: int) -> int:
    """``GTUtility.safeInt(long, int)``: ``number`` as an int, capped at
    ``Integer.MAX_VALUE - margin``."""
    cap = INT_MAX - margin
    return cap if number > cap else wrap_int(number)


def clamp(value: int, lo: int, hi: int) -> int:
    """``GTUtility.clamp(int, int, int)``: ``min(hi, max(value, lo))`` (``hi`` wins when
    ``hi < lo``)."""
    return min(hi, max(value, lo))


def ceil_div(lhs: int, rhs: int) -> int:
    """``GTUtility.ceilDiv(long, long)``: up for a positive quotient, toward zero for a negative
    one (7 / -2 is -3)."""
    q = ldiv(lhs, rhs)
    same_sign = (lhs ^ rhs) > 0
    return q + (1 if same_sign and lhs - q * rhs != 0 else 0)


def add_safe(a: int, b: int) -> int:
    """``GTUtility.addSafe(long, long)``: ``a + b`` clamped to the long range on overflow."""
    r = wrap_long(a + b)
    if a > 0 and b > 0 and r <= 0:
        return LONG_MAX
    if a < 0 and b < 0 and r >= 0:
        return LONG_MIN
    return r


def mul_safe(a: int, b: int) -> int:
    """``GTUtility.mulSafe(long, long)``: ``a * b`` clamped to the long range on overflow."""
    r = a * b
    if LONG_MIN <= r <= LONG_MAX:
        return r
    return LONG_MAX if (a > 0) == (b > 0) else LONG_MIN
