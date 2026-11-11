"""``python -m gregtec.api [--config PATH] [--supervised]``: run the core (see ``server``)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from gregtec import config
from gregtec.api import server


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m gregtec.api")
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--supervised", action="store_true")
    args = parser.parse_args(argv)
    try:
        cfg = config.load(args.config)
    except (OSError, ValueError) as exc:
        print(f"Can't read gos.toml: {exc}", file=sys.stderr)
        return 2
    return server.run(cfg, supervised=args.supervised, config_path=args.config)


if __name__ == "__main__":
    raise SystemExit(main())
