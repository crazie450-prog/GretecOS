"""The setup wizard's routes (WO-2.9, D167-D169). Admin (Clayton's ``gos`` commands, on this PC)
and paired devices only (D117): never a client token, never an unpaired browser.

| route                                   | what                                              |
|-----------------------------------------|---------------------------------------------------|
| GET    /api/setup                       | the steps, gos.toml's values, secrets stored or not, done |
| GET    /api/setup/devices               | audio devices (a pluggable provider, fake off Windows) |
| POST   /api/setup/steps/{id}/check      | ``{"values"}``: problems and checklist rows, nothing saved |
| POST   /api/setup/steps/{id}            | ``{"values"}``: checked, then written to gos.toml |
| POST   /api/setup/secrets/{name}        | ``{"value"}``: into the keyring; never echoed     |
| DELETE /api/setup/secrets/{name}        | out of the keyring                                |
| POST   /api/setup/secrets/{name}/test   | a test call with the stored secret                |
| POST   /api/setup/checklist             | ``gos doctor``'s checks again (the re-run)        |
| POST   /api/setup/finish                | no error row: ``[setup] completed_utc`` written   |

A refused request answers ``{"error", "problems"?, "checks"?}`` with its status. No route logs or
returns a secret: the body of a secret's POST is read, stored and dropped.

No ``from __future__ import annotations``: FastAPI reads the route signatures at runtime.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from gregtec import config
from gregtec.api.security import Principal
from gregtec.setup.service import SetupError, SetupService

if TYPE_CHECKING:
    from gregtec.api.app import Core

JsonBody = Callable[[Request], Awaitable[dict[str, Any]]]


def service_of(core: "Core") -> SetupService:
    """The core's wizard service, made on first use."""
    if core.setup is None:
        core.setup = SetupService(
            core.config_path or config.default_path(),
            core.cfg,
            meter=core.meter,
            ai=core.ai,
            on_secret=core.refresh_key,
        )
    return core.setup


def _refused(exc: SetupError) -> JSONResponse:
    return JSONResponse(exc.body, status_code=exc.status)


def add_routes(
    app: FastAPI,
    core: "Core",
    principal: Callable[[Request], Principal],
    trusted: Callable[..., Principal],
    json_body: JsonBody,
) -> None:
    """Add the wizard's routes to the core's app (``create_app`` calls this once)."""
    Trusted = Annotated[Principal, Depends(trusted)]

    async def run(fn: Callable[[], Any]) -> Any:  # noqa: ANN401 - the route's JSON
        try:
            return await asyncio.to_thread(fn)
        except SetupError as exc:
            return _refused(exc)

    @app.get("/api/setup")
    async def setup_state(_: Trusted) -> Any:  # noqa: ANN401
        return await run(service_of(core).describe)

    @app.get("/api/setup/devices")
    async def setup_devices(_: Trusted) -> Any:  # noqa: ANN401
        return await run(service_of(core).devices)

    async def values_of(request: Request) -> dict[str, Any]:
        values = (await json_body(request)).get("values", {})
        if not isinstance(values, dict):
            raise SetupError(400, "values must be a JSON object")
        return values

    @app.post("/api/setup/steps/{step_id}/check")
    async def setup_check(step_id: str, request: Request, _: Trusted) -> Any:  # noqa: ANN401
        try:
            values = await values_of(request)
        except SetupError as exc:
            return _refused(exc)

        def check() -> dict[str, Any]:
            outcome, _ = service_of(core).check(step_id, values)
            return {
                "problems": outcome.problems,
                "checks": [c.as_dict() for c in outcome.checks],
                "blocked": outcome.blocked,
            }

        return await run(check)

    @app.post("/api/setup/steps/{step_id}")
    async def setup_save(step_id: str, request: Request, _: Trusted) -> Any:  # noqa: ANN401
        try:
            values = await values_of(request)
        except SetupError as exc:
            return _refused(exc)
        return await run(lambda: service_of(core).save(step_id, values))

    @app.post("/api/setup/secrets/{name}")
    async def setup_secret(name: str, request: Request, _: Trusted) -> Any:  # noqa: ANN401
        value = (await json_body(request)).get("value")
        return await run(lambda: service_of(core).store_secret(name, value))

    @app.delete("/api/setup/secrets/{name}")
    async def setup_secret_delete(name: str, _: Trusted) -> Any:  # noqa: ANN401
        return await run(lambda: service_of(core).delete_secret(name))

    @app.post("/api/setup/secrets/{name}/test")
    async def setup_secret_test(name: str, _: Trusted) -> Any:  # noqa: ANN401
        return await run(lambda: service_of(core).test_secret(name))

    @app.post("/api/setup/checklist")
    async def setup_checklist(_: Trusted) -> Any:  # noqa: ANN401
        return await run(
            lambda: {"checks": [c.as_dict() for c in service_of(core).checklist()]}
        )

    @app.post("/api/setup/finish")
    async def setup_finish(_: Trusted) -> Any:  # noqa: ANN401
        return await run(service_of(core).finish)
