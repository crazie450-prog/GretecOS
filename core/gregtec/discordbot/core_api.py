"""The bot's calls to the core (WO-9.1): HTTP on ``127.0.0.1:<[core] port>`` with the client
token ``discord``, never through a proxy (as :func:`gregtec.control.call`). Blocking: the bot
runs each call in a worker thread.
"""

from __future__ import annotations

import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Protocol

from gregtec import control

ASK_TIMEOUT_S = 60.0  # the fast lane gives up well within this (D152)
CALL_TIMEOUT_S = 8.0  # every other call: inside the 10 s answer bound
FILE_MAX = 8 * 1024 * 1024  # Discord's attachment limit for a bot without boosts


class CoreError(Exception):
    """The core didn't answer (``status`` None) or answered with an error status."""

    def __init__(self, message: str, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status

    @property
    def offline(self) -> bool:
        return self.status is None

    @property
    def detail(self) -> str:
        """The core's own reason, without the method and path."""
        _, _, rest = str(self).partition(": ")
        code = f"{self.status} "
        if self.status is not None and rest.startswith(code):
            return rest[len(code) :]
        return rest or str(self)


class CoreApi(Protocol):
    def ask(self, text: str) -> dict[str, Any]: ...
    def deep_task(self, task_id: str) -> dict[str, Any]: ...
    def submit_job(
        self, kind: str, spec: dict[str, Any], key: str
    ) -> dict[str, Any]: ...
    def undelivered(self) -> list[dict[str, Any]]: ...
    def delivered(self, job_id: str) -> dict[str, Any]: ...
    def export(self, plan_id: str, fmt: str) -> bytes: ...
    def ledger(self) -> dict[str, Any]: ...
    def live(self) -> dict[str, Any]: ...
    def ai(self) -> dict[str, Any]: ...
    def personas(self) -> dict[str, Any]: ...
    def set_persona(self, name: str) -> dict[str, Any]: ...
    def alerts(self) -> dict[str, Any]: ...
    def set_alerts(self, mode: str, hours: float | None) -> dict[str, Any]: ...
    def journal(self, kind: str, text: str) -> None: ...
    def actions(self) -> dict[str, Any]: ...
    def confirm(self, action_id: int) -> dict[str, Any]: ...
    def cancel(self, action_id: int) -> dict[str, Any]: ...


class HttpCore:
    """:class:`CoreApi` over HTTP."""

    def __init__(self, port: int, token: str) -> None:
        self.port = port
        self.token = token

    def _call(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
        timeout: float = CALL_TIMEOUT_S,
    ) -> dict[str, Any]:
        try:
            return control.call(
                self.port, method, path, token=self.token, body=body, timeout=timeout
            )
        except control.ControlError as exc:
            raise CoreError(str(exc), exc.status) from None

    def ask(self, text: str) -> dict[str, Any]:
        return self._call("POST", "/api/ask", {"text": text}, ASK_TIMEOUT_S)

    def deep_task(self, task_id: str) -> dict[str, Any]:
        return self._call("GET", f"/api/deep/tasks/{urllib.parse.quote(task_id)}")

    def submit_job(self, kind: str, spec: dict[str, Any], key: str) -> dict[str, Any]:
        return self._call("POST", "/api/jobs", {"kind": kind, "spec": spec, "key": key})

    def undelivered(self) -> list[dict[str, Any]]:
        return list(self._call("GET", "/api/jobs?undelivered=1").get("jobs") or [])

    def delivered(self, job_id: str) -> dict[str, Any]:
        return self._call("POST", f"/api/jobs/{urllib.parse.quote(job_id)}/delivered")

    def export(self, plan_id: str, fmt: str) -> bytes:
        """A saved plan's export file (``png``, ``bom``), at most :data:`FILE_MAX` bytes."""
        path = (
            f"/api/plans/{urllib.parse.quote(plan_id)}/export/{urllib.parse.quote(fmt)}"
        )
        request = urllib.request.Request(
            f"http://{control.LOOPBACK}:{self.port}{path}", method="GET"
        )
        request.add_header("Authorization", f"Bearer {self.token}")
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=CALL_TIMEOUT_S) as response:
                data = response.read(FILE_MAX + 1)
        except urllib.error.HTTPError as exc:
            raise CoreError(f"GET {path}: {exc.code}", exc.code) from None
        except OSError as exc:
            raise CoreError(f"GET {path}: no answer ({exc})") from None
        if len(data) > FILE_MAX:
            raise CoreError(f"GET {path}: the file is over Discord's limit", 413)
        return data

    def ledger(self) -> dict[str, Any]:
        return self._call("GET", "/api/ledger")

    def live(self) -> dict[str, Any]:
        return self._call("GET", "/api/base/live")

    def ai(self) -> dict[str, Any]:
        return self._call("GET", "/api/ai")

    def personas(self) -> dict[str, Any]:
        return self._call("GET", "/api/persona")

    def set_persona(self, name: str) -> dict[str, Any]:
        return self._call("POST", "/api/persona", {"id": name})

    def alerts(self) -> dict[str, Any]:
        return self._call("GET", "/api/discord/alerts")

    def set_alerts(self, mode: str, hours: float | None) -> dict[str, Any]:
        return self._call("POST", "/api/discord/alerts", {"mode": mode, "hours": hours})

    def journal(self, kind: str, text: str) -> None:
        self._call("POST", "/api/discord/journal", {"kind": kind, "text": text})

    def actions(self) -> dict[str, Any]:
        return self._call("GET", "/api/actions")

    def confirm(self, action_id: int) -> dict[str, Any]:
        return self._call("POST", f"/api/actions/{int(action_id)}/confirm")

    def cancel(self, action_id: int) -> dict[str, Any]:
        return self._call("POST", f"/api/actions/{int(action_id)}/cancel")
