"""Minecraft 1.7.10's NBT, read-only (WO-1.6): what VisualProspecting's caches and a world's
``data/*.dat`` are written in.

Re-implemented from the format (big-endian named tags, as ``CompressedStreamTools`` writes
them): a file is one named compound, gzip-compressed (``writeCompressed``) or plain, which
VisualProspecting's own reader also accepts. Values come back as plain Python: a compound is a
``dict``, a list a ``list``, a byte array ``bytes`` (unsigned, as Python reads them), an int
array a ``list[int]``, a string ``str`` (Java's modified UTF-8 decoded). Nothing is ever written.

Bad data raises ``ValueError``: a truncated file, an unknown tag, a length past the end, nesting
deeper than Minecraft's own limit (512), or more than :data:`MAX_BYTES` once decompressed.
"""

from __future__ import annotations

import struct
import sys
import zlib
from array import array

MAX_DEPTH = 512  # NBTTagCompound/NBTTagList refuse deeper nesting
MAX_BYTES = (
    256 << 20
)  # decompressed; a VP cache of every vein in a big world is a few MB
GZIP_MAGIC = b"\x1f\x8b"

type Tag = int | float | str | bytes | list[Tag] | dict[str, Tag]

(
    END,
    BYTE,
    SHORT,
    INT,
    LONG,
    FLOAT,
    DOUBLE,
    BYTE_ARRAY,
    STRING,
    LIST,
    COMPOUND,
    INT_ARRAY,
) = range(12)


def decompress(data: bytes, limit: int = MAX_BYTES) -> bytes:
    """``data`` gunzipped if it is gzip, else as is (``Utils.readNBT``'s fallback)."""
    if not data.startswith(GZIP_MAGIC):
        return data
    d = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = d.decompress(data, limit + 1)
    except zlib.error as exc:
        raise ValueError(f"bad gzip data: {exc}") from None
    if len(out) > limit:
        raise ValueError(f"more than {limit:,} bytes once decompressed")
    if not d.eof:
        raise ValueError("gzip data ends early (a partial write?)")
    return out


def read(data: bytes) -> tuple[str, dict[str, Tag]]:
    """The root compound of an NBT file (gzip or plain): (its name, its value)."""
    r = _Reader(decompress(data))
    tag = r.u8()
    if tag != COMPOUND:
        raise ValueError(f"the root tag is type {tag}, not a compound")
    name = r.string()
    try:
        value = r.compound(1)
    except RecursionError:  # Python's stack ends before MAX_DEPTH on some builds
        raise ValueError("NBT nested too deep") from None
    return name, value


class _Reader:
    def __init__(self, data: bytes) -> None:
        self.data = data
        self.pos = 0

    def take(self, n: int) -> bytes:
        end = self.pos + n
        if n < 0 or end > len(self.data):
            raise ValueError(f"truncated NBT at byte {self.pos:,}")
        chunk = self.data[self.pos : end]
        self.pos = end
        return chunk

    def int_(self, fmt: str) -> int:
        value: int = struct.unpack(">" + fmt, self.take(struct.calcsize(fmt)))[0]
        return value

    def float_(self, fmt: str) -> float:
        value: float = struct.unpack(">" + fmt, self.take(struct.calcsize(fmt)))[0]
        return value

    def u8(self) -> int:
        return self.take(1)[0]

    def length(self) -> int:
        n = self.int_("i")
        if n < 0:
            raise ValueError(f"negative length {n} at byte {self.pos - 4:,}")
        return n

    def string(self) -> str:
        raw = self.take(self.int_("H"))
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError:
            pass
        # Java's modified UTF-8: NUL as C0 80, characters past U+FFFF as two 3-byte surrogates.
        text = raw.replace(b"\xc0\x80", b"\x00").decode("utf-8", "surrogatepass")
        return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")

    def compound(self, depth: int) -> dict[str, Tag]:
        if depth > MAX_DEPTH:
            raise ValueError("NBT nested too deep")
        out: dict[str, Tag] = {}
        while (tag := self.u8()) != END:
            name = self.string()
            out[name] = self.value(tag, depth)
        return out

    def value(self, tag: int, depth: int) -> Tag:
        if tag == BYTE:
            return self.int_("b")
        if tag == SHORT:
            return self.int_("h")
        if tag == INT:
            return self.int_("i")
        if tag == LONG:
            return self.int_("q")
        if tag == FLOAT:
            return self.float_("f")
        if tag == DOUBLE:
            return self.float_("d")
        if tag == BYTE_ARRAY:
            return self.take(self.length())
        if tag == STRING:
            return self.string()
        if tag == LIST:
            kind = self.u8()
            n = self.length()
            if kind == END and n:
                raise ValueError("a list of end tags")
            if n > len(self.data) - self.pos:  # every element takes at least a byte
                raise ValueError(f"list length {n:,} past the end")
            return [self.value(kind, depth + 1) for _ in range(n)]
        if tag == COMPOUND:
            return self.compound(depth + 1)
        if tag == INT_ARRAY:
            n = self.length()
            ints = array("i", self.take(4 * n))
            if sys.byteorder == "little":
                ints.byteswap()
            return ints.tolist()
        raise ValueError(f"unknown NBT tag type {tag} at byte {self.pos - 1:,}")
