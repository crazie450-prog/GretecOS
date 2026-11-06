"""Run the core: ``python -m gregtec.api`` (the supervisor's child) or ``gos serve --core-only``.

- Binds ``127.0.0.1:<[core] port>`` itself (:func:`bind`), so no other program, gos-dev's
  included, can bind the same address beside it. A taken port exits with
  :data:`EXIT_PORT_IN_USE`, which the supervisor shows on the health strip.
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

import uvicorn

from gregtec import applog, control
from gregtec.api.app import Core, create_app
from gregtec.api.security import Auth
from gregtec.config import LOOPBACK, Config
from gregtec.llm.usage import UsageMeter
from gregtec.store import DB_NAME, Store

log = logging.getLogger("gregtec.api")

EXIT_PORT_IN_USE = 3
SUPERVISOR_TOKEN_ENV = "GOS_SUPERVISOR_TOKEN"


def bind(port: int) -> socket.socket:
    """A listening socket on 127.0.0.1:``port`` that no other program can share.

    Windows: no socket options. A specific address bound without SO_REUSEADDR can't be bound
    again by any socket, from any account, while it is open (Windows' enhanced socket security;
    Microsoft's "Using SO_REUSEADDR and SO_EXCLUSIVEADDRUSE"). SO_REUSEADDR, which uvicorn would
    set, lets another account's socket take the address, and SO_EXCLUSIVEADDRUSE can keep a
    restart from binding until old connections end. POSIX: SO_REUSEADDR only lets a restart bind
    past old TIME_WAIT connections.
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


def _stop_on_stdin(server: uvicorn.Server) -> None:
    try:
        for line in sys.stdin.buffer:
            if line.strip() == b"stop":
                break
    except (OSError, ValueError, AttributeError):
        pass
    log.info("supervisor asked the core to stop")
    server.should_exit = True


def run(cfg: Config, *, supervised: bool = False) -> int:
    applog.setup("core", cfg.logs_dir)
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
    core = Core(
        cfg=cfg,
        store=store,
        auth=Auth(store, admin_token),
        meter=UsageMeter(
            store, cfg.monthly_included_usd, cfg.credit_alerts_pct, cfg.cycle_day
        ),
        supervisor_token=supervisor_token,
    )
    server = uvicorn.Server(
        uvicorn.Config(
            create_app(core),
            log_config=None,  # keep applog's JSON handlers
            proxy_headers=False,  # request.client stays the real peer
            server_header=False,
            ws="websockets-sansio",
            lifespan="on",
            timeout_graceful_shutdown=3,
        )
    )

    def shutdown() -> None:
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
        threading.Thread(target=_stop_on_stdin, args=(server,), daemon=True).start()
    log.info(
        "core listening",
        extra={"port": cfg.core_port, "supervised": supervised, "pid": os.getpid()},
    )
    try:
        server.run(sockets=[sock])
    finally:
        control.remove_run_file(run_file, os.getpid())
        store.close()
        sock.close()
    log.info("core stopped")
    return 0
