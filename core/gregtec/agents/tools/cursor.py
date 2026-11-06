"""The ``more`` cursor: the next page of a tool result, with no state kept (plan section 5.3).

A cursor is ``<payload>.<mac>``, both URL-safe base64 without padding. The payload is canonical
JSON of the tool's name, its validated arguments, the offset of the next page and the DB's
epoch (the newest export run and quest load, so a rebuilt DB is never paged into). The MAC is
HMAC-SHA256 over the payload with a key made per :class:`Cursors` (the core makes one per
process), cut to 128 bits. A cursor that was changed in any way, or that another process
issued, is refused; the tool then has to be called again.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any

MAX_LENGTH = 4096
VERSION = 1
_DOMAIN = b"gos-cursor/1\n"
_SHAPE = re.compile(r"[A-Za-z0-9_-]+\.[A-Za-z0-9_-]{22}")


class CursorError(ValueError):
    """Not a cursor this session issued, or a changed one."""


@dataclass(frozen=True)
class Page:
    tool: str
    args: dict[str, Any]
    offset: int
    epoch: str


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Cursors:
    def __init__(self, key: bytes | None = None) -> None:
        self._key = key if key is not None else secrets.token_bytes(32)

    def _mac(self, payload: str) -> str:
        digest = hmac.new(
            self._key, _DOMAIN + payload.encode("ascii"), hashlib.sha256
        ).digest()
        return _b64(digest[:16])

    def issue(self, tool: str, args: dict[str, Any], offset: int, epoch: str) -> str:
        body = {"a": args, "e": epoch, "o": offset, "t": tool, "v": VERSION}
        text = json.dumps(
            body, sort_keys=True, separators=(",", ":"), ensure_ascii=True
        )
        payload = _b64(text.encode("ascii"))
        return f"{payload}.{self._mac(payload)}"

    def open(self, cursor: str) -> Page:
        refused = CursorError(
            "this cursor wasn't issued by this session or was changed; call the tool again"
        )
        if len(cursor) > MAX_LENGTH or not _SHAPE.fullmatch(cursor):
            raise refused
        payload, mac = cursor.split(".")
        if not hmac.compare_digest(mac, self._mac(payload)):
            raise refused
        try:
            body = json.loads(_unb64(payload))
        except (binascii.Error, ValueError):
            raise refused from None
        if not (
            isinstance(body, dict)
            and body.get("v") == VERSION
            and isinstance(body.get("t"), str)
            and isinstance(body.get("a"), dict)
            and type(body.get("o")) is int
            and body["o"] >= 0
            and isinstance(body.get("e"), str)
        ):
            raise refused
        return Page(body["t"], body["a"], body["o"], body["e"])
