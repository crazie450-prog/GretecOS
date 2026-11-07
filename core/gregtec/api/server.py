"""Run the core: ``python -m gregtec.api`` (the supervisor's child) or ``gos serve --core-only``.

- Binds ``127.0.0.1:<[core] port>`` itself (:func:`bind`), so no other account's program,
  gos-dev's included, can bind the same address beside it. A taken port exits with
  :data:`EXIT_PORT_IN_USE`, which the supervisor shows on the health strip.
- Refuses to start (exit 2) with a bad service key or, as gos-dev, Clayton's ports or dashboard
  name (``config.service_problems``), and elevated (exit 4, ``supervisor.elevation_problem``).
- No access log: it would put every query string, and the supervisor's probe every 2 s, into
  ``GOS_HOME/logs/core.log``, which gos-dev reads (D82). Refused Host/Origin checks are logged
  instead.
- Makes a fresh admin token and writes it to ``GOS_HOME/run/core.json`` for Clayton's ``gos``
  commands. ``--supervised``: takes the supervisor's status token from ``GOS_SUPERVISOR_TOKEN``
  (and removes it from the environment, so nothing the core starts inherits it), and stops when
  its stdin closes or reads ``stop``, so a core never outlives its supervisor.
"""

from __future__ import annotations

import logging
import os
import secrets
import socket
import sys
import threading
import time
from collections.abc import Callable

import uvicorn

from gregtec import applog, config, control, supervisor
from gregtec.agents import ask
from gregtec.agents.deep import DeepLaneService
from gregtec.api.app import MAX_MESSAGE, Core, create_app
from gregtec.api.security import Auth
from gregtec.config import LOOPBACK, Config
from gregtec.llm.summarise import HaikuSummariser
from gregtec.llm.usage import UsageMeter
from gregtec.store import DB_NAME, Store
from gregtec.vault.curator import Curator
from gregtec.vault.scheduler import Maintenance, game_running

log = logging.getLogger("gregtec.api")

EXIT_PORT_IN_USE = 3
SUPERVISOR_TOKEN_ENV = "GOS_SUPERVISOR_TOKEN"


def bind(port: int) -> socket.socket:
    """A listening socket on 127.0.0.1:``port`` that no other account's program can share.

    Windows: no socket options. While it is open, a socket of another account (gos-dev's) can't
    bind the same address, with or without SO_REUSEADDR (Windows' enhanced socket security;
    Microsoft's "Using SO_REUSEADDR and SO_EXCLUSIVEADDRUSE"); one of the same account still
    could with SO_REUSEADDR, which only SO_EXCLUSIVEADDRUSE would stop. SO_REUSEADDR on our own
    socket, which uvicorn would set, would let another account's socket take the address too, and
    SO_EXCLUSIVEADDRUSE can keep a restart from binding until old connections end. POSIX:
    SO_REUSEADDR only lets a restart bind past old TIME_WAIT connections.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if sys.platform != "win32":
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((LOOPBACK, port))
        sock.listen(128)
    except OSError:
        sock.close()
        raise
    return sock


def _stop_on_stdin(stop: Callable[[], None]) -> None:
    try:
        for line in sys.stdin.buffer:
            if line.strip() == b"stop":
                break
    except (OSError, ValueError, AttributeError):
        pass
    log.info("supervisor asked the core to stop")
    stop()


def maintenance(cfg: Config, store: Store, meter: UsageMeter) -> Maintenance:
    """Idle maintenance of ``GOS_HOME/vault`` and the DB (WO-2.5, plan 6.6)."""
    curator = Curator(
        vault=cfg.gos_home / "vault",
        db=cfg.data_dir / DB_NAME,
        backups=cfg.data_dir / "backups",
        summariser=HaikuSummariser.from_keyring,
        meter=meter,
        keep_backups=cfg.keep_backups,
    )
    return Maintenance(
        curator, store, game=lambda: game_running(cfg), idle_minutes=cfg.idle_minutes
    )


def run(cfg: Config, *, supervised: bool = False) -> int:
    applog.setup("core", cfg.logs_dir)
    if (why := supervisor.elevation_problem()) is not None:
        log.error("refusing to start", extra={"why": why})
        return supervisor.EXIT_ELEVATED
    if problems := config.service_problems(cfg):
        for problem in problems:
            log.error("refusing to start: fix gos.toml", extra={"why": problem})
        return 2
    supervisor_token = (
        os.environ.pop(SUPERVISOR_TOKEN_ENV, None) if supervised else None
    )
    try:
        sock = bind(cfg.core_port)
    except OSError as exc:
        log.error(
            "port in use: another program holds the core's port",
            extra={"port": cfg.core_port, "error": str(exc)},
        )
        return EXIT_PORT_IN_USE
    store = Store(cfg.data_dir / DB_NAME)
    admin_token = secrets.token_urlsafe(32)
    meter = UsageMeter(
        store, cfg.monthly_included_usd, cfg.credit_alerts_pct, cfg.cycle_day
    )
    agents = ask.setup(cfg, store=store)
    core = Core(
        cfg=cfg,
        store=store,
        auth=Auth(store, admin_token),
        meter=meter,
        supervisor_token=supervisor_token,
        maintenance=maintenance(cfg, store, meter),
        asker=agents.asker(),
    )
    if isinstance(agents.deep, DeepLaneService):
        core.deep = agents.deep
        agents.deep.on_change = core.deep_changed
        agents.deep.start()  # warm: its first session connects now (plan 4.4)
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(core),
            log_config=None,  # keep applog's JSON handlers
            proxy_headers=False,  # request.client stays the real peer
            server_header=False,
            access_log=False,  # see the module docstring
            ws="websockets-sansio",
            ws_max_size=MAX_MESSAGE,  # refused as the frame arrives, not after 16 MiB of it
            lifespan="on",
            timeout_graceful_shutdown=3,
        )
    )

    def shutdown() -> None:
        if core.stop_asked is None:  # the session-end deadline counts from here (D143)
            core.stop_asked = time.monotonic()
        server.should_exit = True

    core.shutdown = shutdown
    run_file = cfg.run_dir / control.CORE_FILE
    control.write_run_file(
        run_file,
        {
            "pid": os.getpid(),
            "port": cfg.core_port,
            "token": admin_token,
            "started": int(time.time()),
        },
    )
    if supervised:
        threading.Thread(target=_stop_on_stdin, args=(shutdown,), daemon=True).start()
    log.info(
        "core listening",
        extra={"port": cfg.core_port, "supervised": supervised, "pid": os.getpid()},
    )
    try:
        server.run(sockets=[sock])
    finally:
        control.remove_run_file(run_file, os.getpid())
        agents.close()  # the deep lane's tasks end as stopped, its CLI processes exit
        store.close()
        sock.close()
    log.info("core stopped")
    return 0
