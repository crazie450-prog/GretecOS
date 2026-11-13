"""NBT read with its types kept, as NEI's ``NBTJson`` writes them (WO-6.1, D257).

GOS keys an item by registry name, meta and the hash of its NBT in NEI's typing (D104), so an
item in the player's inventory hashes as the same item in the recipe DB and in quest tasks
(:func:`gregtec.ingest.quests.nbt_json` does the same from BetterQuesting's typed JSON): ints
bare, byte/short/long as ``"<n>B"``/``"<n>S"``/``"<n>L"``, float and double as Java prints them
plus ``F``/``D``, lists and int arrays as arrays, byte arrays as arrays of ``"<n>B"``, an empty
list or array as its ``__custom_type``. Parsing is :mod:`gregtec.ingest.nbt`'s (re-implemented
from the format); only how each value comes back differs.
"""

from __future__ import annotations

import math
import struct
import sys
from array import array
from decimal import Decimal

from gregtec.ingest import nbt

type Json = dict[str, Json] | list[Json] | str | int


def java_float(value: float, single: bool) -> str:
    """``Float.toString`` / ``Double.toString``: the shortest digits that read back as the same
    value, plain between 10^-3 and 10^7, else ``d.dddE<n>``; always a digit after the point."""
    if math.isnan(value):
        return "NaN"
    if math.isinf(value):
        return "Infinity" if value > 0 else "-Infinity"
    if value == 0:
        return "-0.0" if math.copysign(1.0, value) < 0 else "0.0"
    if single:
        text = repr(value)
        for precision in range(1, 10):
            text = f"{value:.{precision}e}"
            if struct.unpack(">f", struct.pack(">f", float(text)))[0] == value:
                break
    else:
        text = repr(value)
    sign_bit, digit_tuple, exp = Decimal(text).normalize().as_tuple()
    assert isinstance(exp, int)
    digits = "".join(map(str, digit_tuple))
    exponent = len(digits) + exp - 1  # of the first digit
    sign = "-" if sign_bit else ""
    if 1e-3 <= abs(value) < 1e7:
        point = exponent + 1  # digits before the decimal point
        if point <= 0:
            whole, frac = "0", "0" * -point + digits
        elif point >= len(digits):
            whole, frac = digits + "0" * (point - len(digits)), "0"
        else:
            whole, frac = digits[:point], digits[point:]
        return f"{sign}{whole}.{frac}"
    frac = digits[1:] or "0"
    return f"{sign}{digits[0]}.{frac}E{exponent}"


class _TypedReader(
    nbt._Reader
):  # the parser is the shared one; only values are typed here
    def value(self, tag: int, depth: int) -> Json:  # type: ignore[override]
        if tag == nbt.BYTE:
            return f"{self.int_('b')}B"
        if tag == nbt.SHORT:
            return f"{self.int_('h')}S"
        if tag == nbt.INT:
            return self.int_("i")
        if tag == nbt.LONG:
            return f"{self.int_('q')}L"
        if tag == nbt.FLOAT:
            return java_float(self.float_("f"), True) + "F"
        if tag == nbt.DOUBLE:
            return java_float(self.float_("d"), False) + "D"
        if tag == nbt.BYTE_ARRAY:
            raw = self.take(self.length())
            if not raw:
                return {"__custom_type": "net.minecraft.nbt.NBTTagByteArray"}
            return [f"{b - 256 if b > 127 else b}B" for b in raw]
        if tag == nbt.STRING:
            return self.string()
        if tag == nbt.LIST:
            kind = self.u8()
            n = self.length()
            if kind == nbt.END and n:
                raise ValueError("a list of end tags")
            if n > len(self.data) - self.pos:
                raise ValueError(f"list length {n:,} past the end")
            if n == 0:
                return {"__custom_type": "net.minecraft.nbt.NBTTagList"}
            return [self.value(kind, depth + 1) for _ in range(n)]
        if tag == nbt.COMPOUND:
            return self.compound(depth + 1)  # type: ignore[return-value]
        if tag == nbt.INT_ARRAY:
            n = self.length()
            ints = array("i", self.take(4 * n))
            if sys.byteorder == "little":
                ints.byteswap()
            if n == 0:
                return {"__custom_type": "net.minecraft.nbt.NBTTagIntArray"}
            return ints.tolist()
        raise ValueError(f"unknown NBT tag type {tag} at byte {self.pos - 1:,}")


def read(data: bytes) -> dict[str, Json]:
    """The root compound of an NBT file (gzip or plain), typed as NEI's ``NBTJson``."""
    r = _TypedReader(nbt.decompress(data))
    if r.u8() != nbt.COMPOUND:
        raise ValueError("the root tag isn't a compound")
    r.string()
    try:
        return r.compound(1)  # type: ignore[return-value]
    except RecursionError:
        raise ValueError("NBT nested too deep") from None


def num(value: object) -> float | None:
    """A typed number (``12``, ``"3B"``, ``"1.5D"``) as a Python number, else None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return value
    if isinstance(value, str) and value[-1:] in ("B", "S", "L", "F", "D"):
        try:
            return float(value[:-1]) if value[-1] in "FD" else int(value[:-1])
        except ValueError:
            return None
    return None
