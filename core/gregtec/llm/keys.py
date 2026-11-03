"""Secrets in the OS keyring (plan section 4.6): Windows Credential Manager on the PC.

Each secret is its own keyring service, ``<SERVICE>/<name>``, with the secret's name as the
user name, so the Anthropic key is a Generic credential named ``gregtec-os/anthropic`` with
user ``anthropic``. A shared service would make Windows keyring shuffle entries between
targets as secrets are added.
Credential Manager is per Windows account: ``gos-dev`` can't read Clayton's entries.
Nothing here prints or logs a secret, and no error message includes one.
"""

from __future__ import annotations

import keyring
import keyring.errors

SERVICE = "gregtec-os"
ANTHROPIC = "anthropic"
# The secrets `gos key` manages. The Discord token and any cloud TTS key join in later WOs.
NAMES = (ANTHROPIC,)


class MissingKeyError(LookupError):
    def __init__(self, name: str) -> None:
        super().__init__(
            f"no {name} key is stored in this Windows account's Credential Manager. "
            f"Store one with: uv run gos key set {name}"
        )


def backend_chain() -> list[str]:
    """The keyring backends in the order they are tried, by class name.

    keyring picks ``ChainerBackend`` when more than one backend is usable; it then reads and
    writes through those backends in priority order, so the first one is where a key goes.
    """
    backend = keyring.get_keyring()
    chained = getattr(backend, "backends", None)
    return [type(b).__name__ for b in chained] if chained else [type(backend).__name__]


def target(name: str) -> str:
    """The keyring service (the Credential Manager target name) that holds ``name``."""
    return f"{SERVICE}/{name}"


def get_key(name: str) -> str:
    """The stored secret. Raises ``MissingKeyError`` if there is none."""
    value = keyring.get_password(target(name), name)
    if not value:
        raise MissingKeyError(name)
    return value


def has_key(name: str) -> bool:
    return bool(keyring.get_password(target(name), name))


def set_key(name: str, value: str) -> bool:
    """Store the secret. Returns True if it replaced one.

    The old one is deleted first. Windows keyring would otherwise copy it to a second target,
    ``<name>@<target>``, before writing, and the old key would stay stored there.
    """
    replaced = has_key(name)
    if replaced:
        delete_key(name)
    keyring.set_password(target(name), name, value)
    return replaced


def delete_key(name: str) -> bool:
    """Remove the secret. Returns False if none was stored."""
    try:
        keyring.delete_password(target(name), name)
    except keyring.errors.PasswordDeleteError:
        return False
    return True


def anthropic_key_problem(value: str) -> str | None:
    """Why ``value`` doesn't look like a Claude API key, or None. A loose shape check only."""
    if not value.startswith("sk-ant-"):
        return "an Anthropic API key starts with sk-ant-"
    if value.startswith("sk-ant-admin"):
        return "that is an Admin API key; create a regular API key in the gregtec workspace"
    if any(c.isspace() for c in value) or len(value) < 40:
        return "the key looks cut off or has spaces in it; copy it again"
    return None
