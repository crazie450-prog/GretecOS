"""The phone's way in: Tailscale Serve to the core, tailnet only, never Funnel (WO-9.3, D53, D301).

``gos tailnet setup|check|off`` drive the ``tailscale`` CLI on Clayton's PC. **Clayton runs
setup and off himself** (they change his real Tailscale node; as gos-dev they refuse), from
``GOS_APP`` as every Play-adjacent script (plan 4.6). ``check`` only reads, so anyone may run it.

- ``setup``: Tailscale must be running and logged in. It reads the serve config first and
  **refuses while Funnel is on for any port** (Funnel would put the dashboard on the public
  internet, PM06), refuses a raw TCP forward to the core (Serve's HTTP handling, and so the
  core's Funnel check, doesn't apply to one), and refuses to replace another handler on the PC's
  HTTPS port 443. Then
  ``tailscale serve --bg --https=443 http://127.0.0.1:<[core] port>``, read back: the handler
  must proxy to the core and Funnel must still be off, else the handler is removed again.
  It prints the tailnet name for ``[core] allowed_hosts`` (it never writes gos.toml).
- ``check``: Tailscale running, Funnel off, no raw TCP forward to the core, the handler in
  place, and the name in ``[core] allowed_hosts``; exit 0 only when all hold.
- ``off``: removes the handler (``tailscale serve --https=443 off``), only when it is GOS's
  (it proxies to the core); something else on 443 is left alone.

**Never Funnel.** No code path here passes ``funnel`` to the CLI (:meth:`Tailscale.run` refuses
any such argument), ``--funnel`` is refused, and ``setup`` and ``check`` catch a Funnel on any
port. As a second layer the core refuses every request that Tailscale marks as having come
through an **HTTP** Funnel (``Tailscale-Funnel-Request``, ``gregtec.api.security.refusal``).
A **TCP** Funnel (``tailscale funnel --tcp`` / ``--tls-terminated-tcp``) passes bytes on
unmarked, so the core can't tell it from the tailnet: only ``gos tailnet check`` (Funnel on, a
raw TCP forward to the core) catches that one.

What Tailscale does here, from its documentation and source (V28; [ts-serve] tailscale.com/kb/1312/serve,
[ts-cli] tailscale.com/kb/1242/tailscale-serve, [ts-src] github.com/tailscale/tailscale
``ipn/ipnlocal/serve.go``, read 2026-11-18): Serve shares a local service "within your
tailnet" only, Funnel is the separate public command [ts-cli]; it needs HTTPS certificates
enabled for the tailnet [ts-serve]; proxies go to ``http://127.0.0.1`` [ts-cli]; the proxied
request keeps the browser's ``Host`` (``r.Out.Host = r.In.Host``), with ``X-Forwarded-Host``,
``X-Forwarded-Proto: https`` and ``X-Forwarded-For`` added, and for Funnel traffic
``Tailscale-Funnel-Request: ?1``, which Tailscale strips from what a client sends [ts-src].
So the core sees ``Host: <pc>.<tailnet>.ts.net`` (no port: 443 is the default) and an
``Origin`` of ``https://<pc>.<tailnet>.ts.net``.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from gregtec import config

COMMANDS = ("tailnet",)
HTTPS_PORT = 443
TIMEOUT_S = 60  # serve may wait on Tailscale's control plane for the certificate
EXIT_PROBLEM = 1
EXIT_REFUSED = 3


class TailnetError(Exception):
    """A step that can't go on; the message says what to do."""


class FunnelRefused(TailnetError):
    """Something would turn Funnel on, or Funnel is on: GOS never runs with Funnel (PM06)."""


def _default_cli() -> list[str] | None:
    found = shutil.which("tailscale")
    if found:
        return [found]
    if sys.platform == "win32":
        exe = Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Tailscale"
        if (exe / "tailscale.exe").is_file():
            return [str(exe / "tailscale.exe")]
    return None


@dataclass
class Tailscale:
    """The ``tailscale`` CLI, as a command prefix (tests pass a fake: ``[python, fake.py]``)."""

    argv: Sequence[str]
    runner: Callable[..., subprocess.CompletedProcess[str]] = field(
        default=subprocess.run
    )

    def run(self, *args: str) -> str:
        """The command's stdout; :class:`TailnetError` when it fails."""
        if any("funnel" in a.lower() for a in args):
            raise FunnelRefused("GOS never runs tailscale funnel (PM06, D53)")
        try:
            done = self.runner(
                [*self.argv, *args],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=TIMEOUT_S,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            # The CLI waits when Serve or HTTPS isn't enabled for the tailnet, after printing
            # the admin-console link that enables it: show what it said.
            said = exc.stdout or b""
            text = said.decode("utf-8", "replace") if isinstance(said, bytes) else said
            tail = "\n".join(text.strip().splitlines()[-5:])
            raise TailnetError(
                f"tailscale {' '.join(args)} timed out after {TIMEOUT_S} s"
                + (f"; it said:\n{tail}" if tail else "")
            ) from None
        except OSError as exc:
            raise TailnetError(f"tailscale {args[0]} didn't run: {exc}") from None
        if done.returncode != 0:
            said = (done.stderr or done.stdout).strip().splitlines()
            raise TailnetError(
                f"tailscale {' '.join(args)} failed: {said[-1] if said else done.returncode}"
            )
        return done.stdout

    def json(self, *args: str) -> dict[str, Any]:
        out = self.run(*args).strip()
        if not out:
            return {}  # serve status prints nothing when there is no serve config
        try:
            value = json.loads(out)
        except json.JSONDecodeError:
            raise TailnetError(f"tailscale {' '.join(args)} gave no JSON") from None
        return value if isinstance(value, dict) else {}


# --- reading Tailscale's state ------------------------------------------------------------------


def node_name(status: Mapping[str, Any]) -> str:
    """This PC's tailnet name (``pc.tail1234.ts.net``) from ``tailscale status --json``."""
    if status.get("BackendState") != "Running":
        raise TailnetError(
            "Tailscale isn't running and logged in here: open Tailscale and log in "
            f"(state {status.get('BackendState') or 'unknown'})"
        )
    self_ = status.get("Self")
    name = self_.get("DNSName") if isinstance(self_, dict) else None
    if not isinstance(name, str) or not name.strip("."):
        raise TailnetError(
            "this PC has no tailnet name: turn on MagicDNS in the Tailscale admin console"
        )
    return name.strip(".").lower()


def funnels(serve: Mapping[str, Any]) -> list[str]:
    """Every ``host:port`` Funnel is on for, foreground sessions included."""
    found: list[str] = []
    allow = serve.get("AllowFunnel")
    if isinstance(allow, dict):
        found += sorted(str(hp) for hp, on in allow.items() if on)
    fore = serve.get("Foreground")
    if isinstance(fore, dict):
        for sub in fore.values():
            if isinstance(sub, dict):
                found += funnels(sub)
    return found


def handler(serve: Mapping[str, Any], name: str) -> Mapping[str, Any] | None:
    """The handler of ``https://<name>/``, if any."""
    web = serve.get("Web")
    site = web.get(f"{name}:{HTTPS_PORT}") if isinstance(web, dict) else None
    handlers = site.get("Handlers") if isinstance(site, dict) else None
    found = handlers.get("/") if isinstance(handlers, dict) else None
    return found if isinstance(found, dict) else None


def tcp_forwards(serve: Mapping[str, Any], port: int) -> list[str]:
    """Every port with a raw TCP forward to the core's port, foreground sessions included."""
    found: list[str] = []
    tcp = serve.get("TCP")
    if isinstance(tcp, dict):
        for listen, h in tcp.items():
            fwd = h.get("TCPForward") if isinstance(h, dict) else None
            if isinstance(fwd, str) and fwd.rpartition(":")[2] == str(port):
                found.append(str(listen))
    fore = serve.get("Foreground")
    if isinstance(fore, dict):
        for sub in fore.values():
            if isinstance(sub, dict):
                found += tcp_forwards(sub, port)
    return sorted(found)


def target(port: int) -> str:
    return f"http://{config.LOOPBACK}:{port}"


def proxies_to_core(found: Mapping[str, Any] | None, port: int) -> bool:
    if found is None:
        return False
    proxy = str(found.get("Proxy") or "").rstrip("/")
    return proxy in (target(port), f"{config.LOOPBACK}:{port}", str(port))


def no_funnel(serve: Mapping[str, Any]) -> None:
    on = funnels(serve)
    if on:
        fixes = "; ".join(f"tailscale funnel --https={_port(hp)} off" for hp in on)
        raise FunnelRefused(
            f"Tailscale Funnel is on for {', '.join(on)}: it would put services on the public "
            f"internet. GOS won't run with it. Turn it off first: {fixes}"
        )


def _port(hostport: str) -> str:
    return hostport.rpartition(":")[2] or str(HTTPS_PORT)


# --- the three commands -------------------------------------------------------------------------


def setup(ts: Tailscale, port: int) -> str:
    """Serve ``https://<name>/`` to the core; returns ``<name>``. See the module docstring."""
    name = node_name(ts.json("status", "--json"))
    before = ts.json("serve", "status", "--json")
    no_funnel(before)
    raw = tcp_forwards(before, port)
    if raw:
        fixes = "; ".join(f"tailscale serve --tcp={p} off" for p in raw)
        raise TailnetError(
            f"a raw TCP forward on port {', '.join(raw)} already reaches the core, past Serve's "
            f"HTTPS handling. Remove it first: {fixes}"
        )
    found = handler(before, name)
    if found is not None and not proxies_to_core(found, port):
        raise TailnetError(
            f"https://{name}/ already serves something else ({dict(found)}); "
            f"remove it first (tailscale serve --https={HTTPS_PORT} off) if GOS should have it"
        )
    if found is None:
        ts.run("serve", "--bg", f"--https={HTTPS_PORT}", target(port))
    after = ts.json("serve", "status", "--json")
    try:
        no_funnel(after)
    except FunnelRefused:
        ts.run("serve", f"--https={HTTPS_PORT}", "off")
        raise
    if not proxies_to_core(handler(after, name), port):
        raise TailnetError(
            f"tailscale serve didn't take: https://{name}/ doesn't proxy to {target(port)}"
        )
    return name


@dataclass(frozen=True)
class Report:
    name: str | None
    lines: tuple[tuple[bool, str], ...]

    @property
    def ok(self) -> bool:
        return all(good for good, _ in self.lines)


def check(ts: Tailscale, cfg: config.Config) -> Report:
    lines: list[tuple[bool, str]] = []
    try:
        name: str | None = node_name(ts.json("status", "--json"))
        lines.append((True, f"Tailscale is running; this PC is {name}"))
    except TailnetError as exc:
        return Report(None, ((False, str(exc)),))
    try:
        serve = ts.json("serve", "status", "--json")
    except TailnetError as exc:
        return Report(name, (*lines, (False, str(exc))))
    on = funnels(serve)
    lines.append(
        (not on, "Funnel is off" if not on else f"Funnel is ON for {', '.join(on)}")
    )
    raw = tcp_forwards(serve, cfg.core_port)
    lines.append(
        (
            not raw,
            "no raw TCP forward to the core"
            if not raw
            else f"a raw TCP forward on port {', '.join(raw)} reaches the core: "
            + "; ".join(f"tailscale serve --tcp={p} off" for p in raw),
        )
    )
    lines.append(
        (
            proxies_to_core(handler(serve, name), cfg.core_port),
            f"https://{name}/ proxies to {target(cfg.core_port)}"
            if proxies_to_core(handler(serve, name), cfg.core_port)
            else f"https://{name}/ isn't served to the core: gos tailnet setup",
        )
    )
    hosts = {h.lower() for h in cfg.allowed_hosts}
    lines.append(
        (
            name in hosts,
            f"{name} is in [core] allowed_hosts"
            if name in hosts
            else f'{name} isn\'t in [core] allowed_hosts: add "{name}" there, then restart GOS',
        )
    )
    return Report(name, tuple(lines))


def off(ts: Tailscale, port: int) -> bool:
    """Remove GOS's handler; False when there was none. Refuses another service's handler."""
    name = node_name(ts.json("status", "--json"))
    found = handler(ts.json("serve", "status", "--json"), name)
    if found is None:
        return False
    if not proxies_to_core(found, port):
        raise TailnetError(
            f"https://{name}/ isn't GOS's ({dict(found)}): left alone. "
            f"Remove it yourself (tailscale serve --https={HTTPS_PORT} off) if you mean to"
        )
    ts.run("serve", f"--https={HTTPS_PORT}", "off")
    return True


# --- CLI ----------------------------------------------------------------------------------------


def add_parsers(commands: argparse._SubParsersAction[argparse.ArgumentParser]) -> None:
    p = commands.add_parser(
        "tailnet", help="the phone dashboard over Tailscale Serve (WO-9.3)"
    )
    sub = p.add_subparsers(dest="tailnet_command", required=True)
    for name, text in (
        (
            "setup",
            "serve the dashboard to the tailnet (Clayton runs this; never Funnel)",
        ),
        (
            "check",
            "Tailscale running, the handler in place, Funnel off, the name allowed",
        ),
        ("off", "stop serving the dashboard to the tailnet"),
    ):
        q = sub.add_parser(name, help=text)
        q.add_argument(
            "--config", type=Path, default=None, help="gos.toml (default: GOS_HOME's)"
        )
        # Only so the refusal is explicit: there is no way to turn Funnel on from GOS.
        q.add_argument("--funnel", action="store_true", help=argparse.SUPPRESS)


def run(
    args: argparse.Namespace,
    out: TextIO | None = None,
    ts: Tailscale | None = None,
) -> int:
    out = out if out is not None else sys.stdout
    if getattr(args, "funnel", False):
        print(
            "Refused: GOS never turns on Tailscale Funnel. The dashboard is for your tailnet "
            "only (D53, PM06).",
            file=out,
        )
        return EXIT_REFUSED
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=out)
        return 2
    command = args.tailnet_command
    if command in ("setup", "off") and config.running_as_dev():
        print(
            f"Refused: as {config.DEV_ACCOUNT}. Clayton runs gos tailnet {command} himself "
            "(it changes his Tailscale node); gos tailnet check only reads.",
            file=out,
        )
        return EXIT_REFUSED
    if ts is None:
        argv = _default_cli()
        if argv is None:
            print(
                "No tailscale CLI found: install Tailscale (tailscale.com/download) first.",
                file=out,
            )
            return EXIT_PROBLEM
        ts = Tailscale(argv)
    try:
        if command == "setup":
            name = setup(ts, cfg.core_port)
            print(f"https://{name}/ now proxies to {target(cfg.core_port)}", file=out)
            print("Funnel is off; the dashboard is for your tailnet only.", file=out)
            if name not in {h.lower() for h in cfg.allowed_hosts}:
                print(
                    f'Next: add "{name}" to [core] allowed_hosts in gos.toml, then restart '
                    "GOS (gos stop, then log in again or gos serve).",
                    file=out,
                )
            return 0
        if command == "off":
            removed = off(ts, cfg.core_port)
            print(
                "The dashboard is no longer served to the tailnet."
                if removed
                else "Nothing to remove: the dashboard wasn't served to the tailnet.",
                file=out,
            )
            return 0
        report = check(ts, cfg)
        for good, line in report.lines:
            print(f"{'ok ' if good else 'BAD'} {line}", file=out)
        return 0 if report.ok else EXIT_PROBLEM
    except FunnelRefused as exc:
        print(f"Refused: {exc}", file=out)
        return EXIT_REFUSED
    except TailnetError as exc:
        print(str(exc), file=out)
        return EXIT_PROBLEM
