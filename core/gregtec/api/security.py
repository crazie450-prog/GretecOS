"""Who may talk to the core (plan 4.6, QA:A8, D117).

**Host and Origin.** Every HTTP request and WebSocket handshake must carry a ``Host`` from
``[core] allowed_hosts`` (``127.0.0.1:<port>``, ``localhost:<port>``, later the PC's tailnet name),
which stops DNS rebinding. A request with an ``Origin`` must come from one of those hosts over
http or https, and one a browser marks ``Sec-Fetch-Site: cross-site`` or ``same-site`` is refused,
which stops other web pages, other localhost ports included. Requests without ``Origin`` (local
clients, same-origin GETs) pass this check and still need a credential.

**Credentials.** Each request is authenticated as one of:

- ``admin``: ``Authorization: Bearer`` with the core's admin token, which lives only in memory and
  in ``GOS_HOME/run/core.json`` (Clayton's ``gos`` commands; gos-dev can't read ``run``).
- ``client``: a bearer token issued to a named client (voice, discord, mod, oc). The DB keeps only
  its SHA-256.
- ``device``: a paired browser's ``gos_device`` cookie (HttpOnly, SameSite=Strict, ``Secure`` on
  the tailnet name). The value is ``<device id>.<secret>``; the DB keeps the secret's SHA-256.

**Pairing.** A browser without a cookie asks to pair (``POST /api/pair/start``). The core makes an
8-digit code and shows it only where Clayton is: paired dashboards (monitor 2) and ``gos pair``.
Typing it on the new device (``POST /api/pair/complete``) sets the cookie. A request expires after
5 minutes or 5 wrong codes; at most 3 are pending and 10 may start per 10 minutes. Codes and
tokens are never logged, written to disk or returned to the device that asked.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from starlette.types import ASGIApp, Receive, Scope, Send

from gregtec.store import Store

COOKIE = "gos_device"
COOKIE_MAX_AGE = 400 * 86400  # Chrome's cap on cookie lifetime
PAIR_CODE_DIGITS = 8
PAIR_TTL_S = 300
PAIR_MAX_TRIES = 5
PAIR_MAX_PENDING = 3
PAIR_RATE = (10, 600)  # at most 10 pairing requests per 600 s
LAST_SEEN_EVERY_S = 60
CLIENT_NAME = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
_DEVICE_ID = re.compile(r"^[0-9a-f]{16}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def sha256(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


# --- Host and Origin ----------------------------------------------------------------------------


def host_allowed(host: str | None, allowed: Iterable[str]) -> bool:
    return bool(host) and host.lower() in {a.lower() for a in allowed}


def origin_allowed(origin: str, allowed: Iterable[str]) -> bool:
    """Whether ``origin`` is ``http(s)://<an allowed host>`` with nothing after it."""
    try:
        parts = urlsplit(origin)
        _ = parts.port  # raises ValueError for a bad port
    except ValueError:
        return False
    return (
        parts.scheme in ("http", "https")
        and not parts.username
        and not parts.password
        and parts.path in ("", "/")
        and not parts.query
        and not parts.fragment
        and host_allowed(parts.netloc, allowed)
    )


def refusal(headers: dict[str, str], allowed: Iterable[str]) -> str | None:
    """Why a request with these (lower-case) headers is refused, or None."""
    allowed = tuple(allowed)
    if not host_allowed(headers.get("host"), allowed):
        return "host not allowed"
    origin = headers.get("origin")
    if origin is not None and not origin_allowed(origin, allowed):
        return "origin not allowed"
    if headers.get("sec-fetch-site") in ("cross-site", "same-site"):
        return "cross-site request"
    return None


def _headers(scope: Scope) -> dict[str, str]:
    # A repeated Host or Origin header is refused: the last one wins here, and a second Host is
    # never in allowed_hosts together with a bad one, but a bad one is never silently dropped.
    seen: dict[str, str] = {}
    for raw_key, raw_value in scope.get("headers", []):
        key = raw_key.decode("latin-1").lower()
        value = raw_value.decode("latin-1")
        if key in ("host", "origin") and key in seen and seen[key] != value:
            seen[key] = ""  # conflicting copies: neither is allowed
            continue
        seen[key] = value
    return seen


class HostOriginGuard:
    """ASGI middleware: refuse HTTP requests and WebSocket handshakes that fail :func:`refusal`."""

    def __init__(self, app: ASGIApp, allowed_hosts: Iterable[str]) -> None:
        self.app = app
        self.allowed = tuple(allowed_hosts)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        reason = refusal(_headers(scope), self.allowed)
        if reason is None:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": reason})
            return
        body = json.dumps({"error": reason}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


# --- credentials --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Principal:
    role: str  # "admin" | "client" | "device"
    name: str  # the client's or device's name; "admin" for admin
    device_id: str | None = None


class Auth:
    """Bearer tokens and device cookies, checked against ``client_token`` and ``device``."""

    def __init__(self, store: Store, admin_token: str) -> None:
        if len(admin_token) < 32:
            raise ValueError("the admin token is too short")
        self.store = store
        self._admin = admin_token

    def from_headers(
        self, authorization: str | None, cookie: str | None
    ) -> Principal | None:
        if authorization is not None:
            scheme, _, token = authorization.partition(" ")
            if scheme.lower() != "bearer" or not token.strip():
                return None
            return self.bearer(token.strip())
        if cookie:
            return self.device(cookie)
        return None

    def bearer(self, token: str) -> Principal | None:
        if hmac.compare_digest(token.encode(), self._admin.encode()):
            return Principal("admin", "admin")
        # A lookup by SHA-256 leaks nothing useful through timing: the hash isn't invertible.
        rows = self.store.rows(
            "SELECT name, last_used FROM client_token WHERE token_sha256 = ? AND revoked = 0",
            (sha256(token),),
        )
        if not rows:
            return None
        name, last_used = rows[0]["name"], rows[0]["last_used"]
        now = int(time.time())
        if last_used is None or now - last_used >= LAST_SEEN_EVERY_S:
            with self.store.transaction() as conn:
                conn.execute(
                    "UPDATE client_token SET last_used = ? WHERE name = ?", (now, name)
                )
        return Principal("client", name)

    def device(self, cookie: str) -> Principal | None:
        device_id, _, secret = cookie.partition(".")
        if not _DEVICE_ID.match(device_id) or not secret:
            return None
        rows = self.store.rows(
            "SELECT name, last_seen, token_sha256 FROM device "
            "WHERE id = ? AND paired IS NOT NULL AND COALESCE(revoked, 0) = 0",
            (device_id,),
        )
        if not rows or not rows[0]["token_sha256"]:
            return None
        row = rows[0]
        if not hmac.compare_digest(row["token_sha256"], sha256(secret)):
            return None
        now = int(time.time())
        if row["last_seen"] is None or now - row["last_seen"] >= LAST_SEEN_EVERY_S:
            with self.store.transaction() as conn:
                conn.execute(
                    "UPDATE device SET last_seen = ? WHERE id = ?", (now, device_id)
                )
        return Principal("device", row["name"], device_id)

    # client tokens

    def issue_client_token(self, name: str) -> str:
        """A new token for ``name``, replacing any earlier one. Only its hash is stored."""
        if not CLIENT_NAME.match(name):
            raise ValueError(
                "a client name is lower-case letters, digits and -, starting with a letter"
            )
        token = secrets.token_urlsafe(32)
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO client_token(name, token_sha256, created, last_used, revoked) "
                "VALUES (?, ?, ?, NULL, 0) ON CONFLICT(name) DO UPDATE SET "
                "token_sha256 = excluded.token_sha256, created = excluded.created, "
                "last_used = NULL, revoked = 0",
                (name, sha256(token), int(time.time())),
            )
        return token

    def revoke_client_token(self, name: str) -> bool:
        with self.store.transaction() as conn:
            cur = conn.execute(
                "UPDATE client_token SET revoked = 1 WHERE name = ? AND revoked = 0",
                (name,),
            )
        return cur.rowcount > 0

    def client_tokens(self) -> list[dict[str, Any]]:
        rows = self.store.rows(
            "SELECT name, created, last_used, revoked FROM client_token ORDER BY name"
        )
        return [
            {
                "name": r["name"],
                "created": r["created"],
                "last_used": r["last_used"],
                "revoked": bool(r["revoked"]),
            }
            for r in rows
        ]

    # devices

    def add_device(self, name: str) -> tuple[str, str]:
        """Store a newly paired device. Returns ``(device id, cookie value)``."""
        device_id = secrets.token_hex(8)
        secret = secrets.token_urlsafe(32)
        now = int(time.time())
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO device(id, name, paired, last_seen, revoked, token_sha256) "
                "VALUES (?, ?, ?, ?, 0, ?)",
                (device_id, name, now, now, sha256(secret)),
            )
        return device_id, f"{device_id}.{secret}"

    def revoke_device(self, device_id: str) -> bool:
        with self.store.transaction() as conn:
            cur = conn.execute(
                "UPDATE device SET revoked = 1, token_sha256 = NULL "
                "WHERE id = ? AND COALESCE(revoked, 0) = 0",
                (device_id,),
            )
        return cur.rowcount > 0

    def devices(self) -> list[dict[str, Any]]:
        rows = self.store.rows(
            "SELECT id, name, paired, last_seen, revoked FROM device ORDER BY paired"
        )
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "paired": r["paired"],
                "last_seen": r["last_seen"],
                "revoked": bool(r["revoked"]),
            }
            for r in rows
        ]


# --- pairing ------------------------------------------------------------------------------------


class PairingError(Exception):
    def __init__(self, status: int, reason: str) -> None:
        super().__init__(reason)
        self.status = status
        self.reason = reason


@dataclass
class PairRequest:
    request_id: str
    name: str
    code: str
    created: float
    tries: int = 0

    def expires_in(self, now: float) -> int:
        return max(0, int(self.created + PAIR_TTL_S - now))


def clean_device_name(name: object) -> str:
    if not isinstance(name, str):
        raise PairingError(400, "name must be a string")
    cleaned = " ".join(_CONTROL.sub(" ", name).split())[:40]
    if not cleaned:
        raise PairingError(400, "name must not be empty")
    return cleaned


class Pairing:
    """Pending pairing requests, in memory only. ``on_change`` runs after every change."""

    def __init__(
        self,
        on_change: Callable[[], None] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._pending: dict[str, PairRequest] = {}
        self._starts: list[float] = []
        self._lock = threading.Lock()
        self._clock = clock
        self.on_change = on_change

    def _expire(self, now: float) -> None:
        for rid in [r for r, req in self._pending.items() if req.expires_in(now) == 0]:
            del self._pending[rid]
        self._starts = [t for t in self._starts if now - t < PAIR_RATE[1]]

    def start(self, name: object) -> PairRequest:
        name = clean_device_name(name)
        with self._lock:
            now = self._clock()
            self._expire(now)
            if len(self._starts) >= PAIR_RATE[0]:
                raise PairingError(429, "too many pairing requests; try again later")
            if len(self._pending) >= PAIR_MAX_PENDING:
                raise PairingError(429, "too many pending pairing requests")
            self._starts.append(now)
            request = PairRequest(
                request_id=secrets.token_hex(8),
                name=name,
                code=f"{secrets.randbelow(10**PAIR_CODE_DIGITS):0{PAIR_CODE_DIGITS}d}",
                created=now,
            )
            self._pending[request.request_id] = request
        self._changed()
        return request

    def complete(self, request_id: object, code: object) -> str:
        """The device name if ``code`` is right. Raises :class:`PairingError` otherwise."""
        if not isinstance(request_id, str) or not isinstance(code, str):
            raise PairingError(400, "request_id and code must be strings")
        typed = re.sub(r"[\s-]", "", code)
        changed = False
        try:
            with self._lock:
                self._expire(self._clock())
                request = self._pending.get(request_id)
                if request is None:
                    raise PairingError(
                        404, "no such pairing request (expired or denied?)"
                    )
                if not hmac.compare_digest(typed.encode(), request.code.encode()):
                    request.tries += 1
                    if request.tries >= PAIR_MAX_TRIES:
                        del self._pending[request_id]
                        changed = True
                        raise PairingError(
                            403, "wrong code; this pairing request is closed"
                        )
                    raise PairingError(403, "wrong code")
                del self._pending[request_id]
                changed = True
                return request.name
        finally:
            if changed:
                self._changed()

    def deny(self, request_id: str) -> bool:
        with self._lock:
            found = self._pending.pop(request_id, None) is not None
        if found:
            self._changed()
        return found

    def pending(self) -> list[dict[str, Any]]:
        """For the trusted displays only (paired devices, admin): includes the codes."""
        with self._lock:
            now = self._clock()
            self._expire(now)
            return [
                {
                    "request_id": r.request_id,
                    "name": r.name,
                    "code": r.code,
                    "expires_in": r.expires_in(now),
                }
                for r in sorted(self._pending.values(), key=lambda r: r.created)
            ]

    def _changed(self) -> None:
        if self.on_change is not None:
            self.on_change()
