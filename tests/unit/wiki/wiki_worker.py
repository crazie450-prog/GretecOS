"""Another process asking the fake wiki (test_wiki_api's cross-process test): ``API LOCK N
INTERVAL`` sends N page requests through a client that shares the lock file LOCK."""

from __future__ import annotations

import sys
from pathlib import Path

from gregtec.wiki.api import WikiApi


def main(argv: list[str]) -> int:
    api, lock, n, interval = argv
    client = WikiApi(api, interval_s=float(interval), lock_path=Path(lock))
    for _ in range(int(n)):
        client.pages(["Pyrolyse Oven"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
