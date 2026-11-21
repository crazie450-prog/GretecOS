"""Model providers for one job: a system text and a user text in, one JSON answer out
(WO-10.11, D374). The shell bake-off (:mod:`gregtec.build.shell_bakeoff`) is the only caller.

A :class:`Designer` has no tools, no conversation and no files: :meth:`Designer.complete` sends
the two texts and a JSON schema once and returns the answer's text, its token usage and why it
stopped (:class:`Reply`), or raises :class:`ProviderError`. :func:`call` adds the one retry
policy every provider gets.

- :class:`AnthropicDesigner`: the Messages API through the ``anthropic`` SDK the repo already
  uses (streamed, ``output_config.format`` JSON schema, the SDK's own retries off).
- :class:`OpenAIDesigner`: OpenAI's Responses API (``POST https://api.openai.com/v1/responses``)
  over plain HTTPS with the standard library, no SDK: ``instructions`` + ``input``,
  ``text.format`` ``{type: json_schema, name, schema, strict: true}``, ``max_output_tokens``
  (visible and reasoning tokens together), optional ``reasoning.effort``, ``store: false``,
  ``stream: true`` (server-sent events; the last one, ``response.completed``, ``.incomplete`` or
  ``.failed``, carries the whole response and its ``usage``, which may be null: such an answer
  is :attr:`Reply.measured` false). A redirect is never followed. Built from OpenAI's own API
  reference and guides (Responses "Create a model response", Structured Outputs, Error codes,
  Rate limits) as read on 2026-11-20; the host is contacted only when a bake-off names an
  OpenAI model.

Neither sends ``tools``. The key is passed in by the caller (from the keyring), never logged,
and :func:`scrub` takes it out of any error text an API sends back.
"""

from __future__ import annotations

import http.client
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

import anthropic
import httpx2  # the anthropic SDK's HTTP client: its errors come up raw from inside a stream

from gregtec.llm.prices import Usage

OPENAI_URL = "https://api.openai.com/v1/responses"
OPENAI_HOST = "api.openai.com"
ANTHROPIC_HOST = "api.anthropic.com"
TIMEOUT_S = 600.0  # one answer, start to end
READ_S = 180.0  # the longest silence on the connection
CONNECT_S = 10.0
RETRIES = 2  # after the first try, for every provider
BACKOFF_S = (2.0, 8.0)  # the waits without a Retry-After
MAX_WAIT_S = 60.0  # a Retry-After longer than this is not waited for
# An answer's text is bounded in characters, the same for every vendor (the caller's
# ``shell_brief.MAX_REPLY_CHARS``, checked on the text itself). The stream that carries it is
# far larger: one server-sent event per token, each a few hundred bytes of framing around a few
# characters, then the whole response once more in the last event. So the stream's own bounds
# only stop a stream that never ends: MAX_ANSWER_CHARS one-character events of 500 bytes each
# still fit, with room for the last event.
MAX_ANSWER_CHARS = 60_000
MAX_LINE_BYTES = 2_000_000  # one event (the last one carries the whole answer)
MAX_STREAM_BYTES = 32_000_000  # of one answer's whole HTTP body
assert MAX_STREAM_BYTES >= MAX_ANSWER_CHARS * 500 + MAX_LINE_BYTES
MAX_ERROR_BYTES = 65_536
# The effort values each vendor's own page lists (read 2026-11-21): Anthropic's "Effort" page
# (output_config.effort: five levels), OpenAI's "Reasoning" guide (reasoning.effort: "Supported
# values are model-dependent and can include none, minimal, low, medium, high, xhigh, and max").
# Which of them one model takes is the vendor's to say: a value it doesn't take comes back as
# HTTP 400.
EFFORTS = {
    "anthropic": ("low", "medium", "high", "xhigh", "max"),
    "openai": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
}

AUTH, QUOTA, RATE, SERVER, TIMEOUT, NETWORK, CUT_OFF, BAD_REQUEST, TOO_LARGE = (
    "auth",
    "quota",
    "rate_limit",
    "server",
    "timeout",
    "network",
    "cut_off",
    "bad_request",
    "too_large",
)
RETRYABLE = frozenset({RATE, SERVER, TIMEOUT, NETWORK, CUT_OFF})
# After one of these the same model is not asked again in the run: the next call would fail too.
STOPS_MODEL = frozenset({AUTH, QUOTA, BAD_REQUEST})
# A call that failed one of these ways had been sent and may have been answering: the vendor may
# bill what it had made, and no usage came back to count (ProviderError.maybe_billed).
MAYBE_BILLED = frozenset({TIMEOUT, CUT_OFF, TOO_LARGE})
END, MAX_TOKENS, REFUSAL = "end", "max_tokens", "refusal"
# OpenAI's 429 codes that are about money or an assigned limit, not about pace (Error codes).
_QUOTA_CODES = frozenset(
    {
        "credit_balance_exhausted",
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
        "organization_usage_limit_exceeded",
        "insufficient_quota",
    }
)
_KEYLIKE = re.compile(r"sk-[A-Za-z0-9_\-*.]{6,}")


class ProviderError(RuntimeError):
    """A call that gave no answer. ``kind`` is one of the constants above; the message is
    plain words with no key in it. ``maybe_billed``: the answer had (or may have) begun when
    the call failed, so the vendor may bill it although no usage was reported (default: by
    ``kind``, :data:`MAYBE_BILLED`; an error sent inside a begun answer says so itself)."""

    def __init__(
        self,
        kind: str,
        message: str,
        retry_after: float | None = None,
        *,
        maybe_billed: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retry_after = retry_after
        self.maybe_billed = (
            kind in MAYBE_BILLED if maybe_billed is None else maybe_billed
        )


@dataclass(frozen=True)
class Reply:
    text: str
    usage: Usage  # input is the uncached input; output includes thinking or reasoning
    stop: str = END  # END, MAX_TOKENS or REFUSAL
    detail: str = ""  # a refusal's words, clipped
    reasoning_tokens: int = (
        0  # the part of usage.output spent reasoning, where reported
    )
    # False: the vendor sent the answer without its token count, so ``usage`` is zeros that
    # mean "unknown", never "free" (the caller counts such a call at the most it can cost).
    measured: bool = True


class Designer(Protocol):
    provider: str
    model: str

    def complete(
        self, system: str, user: str, schema: Mapping[str, Any], max_output_tokens: int
    ) -> Reply: ...


def scrub(text: str, key: str = "") -> str:
    """``text`` without the key or anything shaped like one, on one line, clipped."""
    if key:
        text = text.replace(key, "[key]")
    return " ".join(_KEYLIKE.sub("[key]", text).split())[:300]


def call(
    designer: Designer,
    system: str,
    user: str,
    schema: Mapping[str, Any],
    max_output_tokens: int,
    *,
    sleep: Callable[[float], None] = time.sleep,
) -> Reply:
    """One answer, with the same retries for every provider: a rate limit, a server error, a
    timeout, a network failure or a cut-off answer is tried again at most :data:`RETRIES`
    times, after the server's ``Retry-After`` when it gives one (never longer than
    :data:`MAX_WAIT_S`), else after :data:`BACKOFF_S`. Anything else is raised at once."""
    for attempt in range(RETRIES + 1):
        try:
            return designer.complete(system, user, schema, max_output_tokens)
        except ProviderError as exc:
            if exc.kind not in RETRYABLE or attempt == RETRIES:
                raise
            wait = (
                exc.retry_after if exc.retry_after is not None else BACKOFF_S[attempt]
            )
            if wait > MAX_WAIT_S:
                raise ProviderError(
                    exc.kind,
                    f"{exc} (asked to wait {wait:.0f} s, longer than {MAX_WAIT_S:.0f} s: "
                    "not waited for)",
                    maybe_billed=exc.maybe_billed,
                ) from None
            sleep(wait)
    raise AssertionError("unreachable")


# --- Anthropic -------------------------------------------------------------------------------


def anthropic_client(key: str) -> anthropic.Anthropic:
    return anthropic.Anthropic(
        api_key=key,
        timeout=anthropic.Timeout(TIMEOUT_S, read=READ_S, connect=CONNECT_S),
        max_retries=0,  # call()'s retries are the only ones, as for every provider
    )


def _retry_after(headers: Any) -> float | None:  # noqa: ANN401 - any header mapping
    try:
        raw = headers.get("retry-after") if headers is not None else None
        value = float(str(raw).strip())
    except (TypeError, ValueError, AttributeError):
        return None
    return value if 0 <= value < 86_400 else None


@dataclass
class AnthropicDesigner:
    """``client`` is ``anthropic.Anthropic`` (or a fake with ``messages.stream``)."""

    client: Any
    model: str
    effort: str | None = None
    provider: str = "anthropic"

    def complete(
        self, system: str, user: str, schema: Mapping[str, Any], max_output_tokens: int
    ) -> Reply:
        output: dict[str, Any] = {"format": {"type": "json_schema", "schema": schema}}
        if self.effort:
            output["effort"] = self.effort
        try:
            with self.client.messages.stream(
                model=self.model,
                max_tokens=max_output_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_config=output,
            ) as stream:
                message = stream.get_final_message()
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            raise ProviderError(
                AUTH,
                f"Anthropic refused the key (HTTP {exc.status_code}). Store a working one "
                "with: uv run gos key set anthropic",
            ) from None
        except anthropic.RateLimitError as exc:
            raise ProviderError(
                RATE,
                "Anthropic says too many requests (HTTP 429)",
                _retry_after(getattr(exc.response, "headers", None)),
            ) from None
        except anthropic.APITimeoutError:
            raise ProviderError(TIMEOUT, "Anthropic did not answer in time") from None
        except anthropic.APIConnectionError:
            raise ProviderError(NETWORK, "Anthropic could not be reached") from None
        except anthropic.APIStatusError as exc:
            text = scrub(str(getattr(exc, "message", "")) or type(exc).__name__)
            # below 400: an error event inside an answer that began with HTTP 200 (overloaded,
            # for one): the server's trouble, worth another try, never "this model can't be used"
            if exc.status_code >= 500 or exc.status_code < 400:
                raise ProviderError(
                    SERVER,
                    f"Anthropic had a server error (HTTP {exc.status_code})",
                    _retry_after(getattr(exc.response, "headers", None)),
                    maybe_billed=exc.status_code < 400,
                ) from None
            if "credit balance" in text.lower():
                raise ProviderError(
                    QUOTA, f"Anthropic says the credits are used up: {text}"
                ) from None
            raise ProviderError(
                BAD_REQUEST,
                f"Anthropic refused the request (HTTP {exc.status_code}): {text}",
            ) from None
        except anthropic.APIError as exc:  # an error event inside the stream
            raise ProviderError(
                CUT_OFF, f"Anthropic's answer broke off ({type(exc).__name__})"
            ) from None
        # The SDK wraps a failed request, not a connection that fails while the answer
        # streams: that comes up as the HTTP client's own error.
        except httpx2.TimeoutException:
            raise ProviderError(
                TIMEOUT, "Anthropic stopped answering part way"
            ) from None
        except httpx2.HTTPError as exc:
            raise ProviderError(
                CUT_OFF, f"Anthropic's answer broke off ({type(exc).__name__})"
            ) from None
        usage = message.usage
        text = "".join(
            b.text for b in message.content if getattr(b, "type", "") == "text"
        )
        used = Usage(
            input=int(getattr(usage, "input_tokens", 0) or 0),
            output=int(getattr(usage, "output_tokens", 0) or 0),
            cache_write=int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
            cache_read=int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        )
        stop = getattr(message, "stop_reason", None)
        if stop == "refusal":
            details = getattr(message, "stop_details", None)
            why = getattr(details, "category", None) or "no category given"
            return Reply("", used, REFUSAL, scrub(f"the model declined ({why})"))
        if stop == "max_tokens":
            return Reply(text, used, MAX_TOKENS)
        return Reply(text, used)


# --- OpenAI ----------------------------------------------------------------------------------


def _error_parts(raw: bytes) -> tuple[str, str, str]:
    """(code, type, message) of an OpenAI error body ``{"error": {...}}``; blanks when it
    isn't one."""
    try:
        err = json.loads(raw.decode("utf-8", "replace")).get("error")
    except (ValueError, AttributeError, RecursionError):
        return "", "", ""
    if not isinstance(err, dict):
        return "", "", ""
    return (
        str(err.get("code") or ""),
        str(err.get("type") or ""),
        str(err.get("message") or ""),
    )


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: urllib's own handler sends the request's headers, the
    ``Authorization`` key with them, to whatever host a 301, 302, 303, 307 or 308 names."""

    def redirect_request(  # type: ignore[override]
        self, *args: object, **kwargs: object
    ) -> None:
        return None  # the 3xx then comes up as an HTTPError


def _loopback(url: str) -> bool:
    return urllib.parse.urlsplit(url).hostname in ("127.0.0.1", "localhost", "::1")


@dataclass
class OpenAIDesigner:
    key: str = field(repr=False)
    model: str = ""
    effort: str | None = None
    url: str = OPENAI_URL
    timeout_s: float = TIMEOUT_S
    read_s: float = READ_S
    provider: str = "openai"
    clock: Callable[[], float] = time.monotonic

    def body(
        self, system: str, user: str, schema: Mapping[str, Any], max_output_tokens: int
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "instructions": system,
            "input": user,
            "max_output_tokens": max_output_tokens,
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "shell_design",
                    "schema": schema,
                    "strict": True,
                }
            },
            "store": False,
            "stream": True,
        }
        if self.effort:
            body["reasoning"] = {"effort": self.effort}
        return body

    def complete(
        self, system: str, user: str, schema: Mapping[str, Any], max_output_tokens: int
    ) -> Reply:
        data = json.dumps(self.body(system, user, schema, max_output_tokens)).encode()
        request = urllib.request.Request(
            self.url,
            data=data,
            method="POST",
            headers={
                "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
                "User-Agent": "gregtec-os",
            },
        )
        # A test's fake server is on loopback: no proxy stands between.
        handlers: list[urllib.request.BaseHandler] = [_NoRedirect()]
        if _loopback(self.url):
            handlers.append(urllib.request.ProxyHandler({}))
        opener = urllib.request.build_opener(*handlers)
        try:
            with opener.open(request, timeout=self.read_s) as r:
                final = self._read(r)
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from None
        except TimeoutError:
            raise ProviderError(TIMEOUT, "OpenAI did not answer in time") from None
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, TimeoutError):
                raise ProviderError(TIMEOUT, "OpenAI did not answer in time") from None
            raise ProviderError(NETWORK, "OpenAI could not be reached") from None
        except (http.client.HTTPException, OSError):  # a reset, a TLS failure, ...
            raise ProviderError(
                CUT_OFF, "OpenAI's answer broke off before it was complete"
            ) from None
        return self._reply(final)

    def _http_error(self, exc: urllib.error.HTTPError) -> ProviderError:
        try:
            raw = exc.read(MAX_ERROR_BYTES)
        except (OSError, http.client.HTTPException):
            raw = b""
        code, kind, message = _error_parts(raw)
        said = scrub(message, self.key)
        tail = f": {said}" if said else ""
        wait = _retry_after(exc.headers)
        if 300 <= exc.code < 400:
            return ProviderError(
                BAD_REQUEST,
                f"OpenAI's address answered with a redirect (HTTP {exc.code}): not followed, "
                "so the key goes nowhere else. Something stands between this PC and "
                f"{OPENAI_HOST} (a proxy, a sign-in page of the network)",
            )
        if exc.code in (401, 403):
            return ProviderError(
                AUTH,
                f"OpenAI refused the key (HTTP {exc.code}){tail}. Store a working one with: "
                "uv run gos key set openai",
            )
        if exc.code == 429:
            if code in _QUOTA_CODES or kind in _QUOTA_CODES:
                return ProviderError(
                    QUOTA,
                    "OpenAI says the account is out of credit or at a spend limit "
                    f"({code or kind}){tail}",
                )
            return ProviderError(
                RATE, f"OpenAI says too many requests (HTTP 429){tail}", wait
            )
        if exc.code == 408:
            return ProviderError(TIMEOUT, "OpenAI timed the request out (HTTP 408)")
        if exc.code >= 500:
            return ProviderError(
                SERVER, f"OpenAI had a server error (HTTP {exc.code}){tail}", wait
            )
        return ProviderError(
            BAD_REQUEST, f"OpenAI refused the request (HTTP {exc.code}){tail}"
        )

    def _read(self, r: Any) -> dict[str, Any]:  # noqa: ANN401 - an HTTPResponse
        """The stream's last word: the ``response`` of ``response.completed``, ``.incomplete``
        or ``.failed``. Bounded in bytes and time; an ``error`` event or an end without a last
        word raises."""
        deadline = self.clock() + self.timeout_s
        size = 0
        while True:
            line = r.readline(MAX_LINE_BYTES + 1)
            if not line:
                raise ProviderError(
                    CUT_OFF, "OpenAI's answer broke off before it was complete"
                )
            size += len(line)
            if len(line) > MAX_LINE_BYTES or size > MAX_STREAM_BYTES:
                raise ProviderError(
                    TOO_LARGE,
                    f"the answer's stream is over {MAX_LINE_BYTES:,} bytes in one event or "
                    f"{MAX_STREAM_BYTES:,} in all: not read further",
                )
            if self.clock() > deadline:
                raise ProviderError(TIMEOUT, "OpenAI did not finish in time")
            if not line.startswith(b"data:"):
                continue
            try:
                event = json.loads(line[5:].decode("utf-8", "replace"))
            except (ValueError, RecursionError):  # not an event: nested too deep is one
                continue
            if not isinstance(event, dict):
                continue
            kind = event.get("type")
            if kind == "error":
                code = str(event.get("code") or "")
                said = scrub(str(event.get("message") or ""), self.key)
                which = RATE if code == "rate_limit_exceeded" else SERVER
                raise ProviderError(
                    which,
                    f"OpenAI sent an error in the answer ({code}): {said}",
                    maybe_billed=True,
                )
            if kind in (
                "response.completed",
                "response.incomplete",
                "response.failed",
            ) and isinstance(event.get("response"), dict):
                found: dict[str, Any] = event["response"]
                return found

    def _reply(self, response: Mapping[str, Any]) -> Reply:
        raw = response.get("usage")
        # The reference lets usage be null. Zeros would then read as "free": say unmeasured.
        measured = (
            isinstance(raw, dict)
            and type(raw.get("input_tokens")) is int
            and type(raw.get("output_tokens")) is int
        )
        if not isinstance(raw, dict):
            raw = {}
        in_details = raw.get("input_tokens_details") or {}
        out_details = raw.get("output_tokens_details") or {}
        cached = int(in_details.get("cached_tokens") or 0)
        written = int(in_details.get("cache_write_tokens") or 0)
        used = Usage(
            # input_tokens counts the cached and cache-written tokens too (its details break
            # it down), so the uncached part is what is left
            input=max(0, int(raw.get("input_tokens") or 0) - cached - written),
            output=int(raw.get("output_tokens") or 0),
            cache_write=written,
            cache_read=cached,
        )
        reasoning = int(out_details.get("reasoning_tokens") or 0)
        text, refusal = [], []
        for item in response.get("output") or []:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            for part in item.get("content") or []:
                if not isinstance(part, dict):
                    continue
                if part.get("type") == "output_text":
                    text.append(str(part.get("text") or ""))
                elif part.get("type") == "refusal":
                    refusal.append(str(part.get("refusal") or ""))
        status = response.get("status")
        if refusal:
            return Reply(
                "",
                used,
                REFUSAL,
                scrub("the model declined: " + " ".join(refusal), self.key),
                reasoning,
                measured,
            )
        if status == "completed":
            return Reply("".join(text), used, END, "", reasoning, measured)
        if status == "incomplete":
            reason = (response.get("incomplete_details") or {}).get("reason")
            if reason == "max_output_tokens":
                return Reply("".join(text), used, MAX_TOKENS, "", reasoning, measured)
            return Reply(
                "",
                used,
                REFUSAL,
                scrub(f"the answer was stopped ({reason or 'no reason given'})"),
                reasoning,
                measured,
            )
        err = response.get("error") or {}
        code = str(err.get("code") or "")
        said = scrub(str(err.get("message") or ""), self.key)
        if code == "rate_limit_exceeded":
            raise ProviderError(
                RATE, f"OpenAI says too many requests: {said}", maybe_billed=True
            )
        if code == "server_error" or status != "failed":
            raise ProviderError(
                SERVER,
                f"OpenAI could not make the answer ({code or status}): {said}",
                maybe_billed=True,
            )
        return Reply(
            "",
            used,
            REFUSAL,
            scrub(f"the request was declined ({code}): {said}"),
            reasoning,
            measured,
        )
