"""``python -m gregtec_voice ...``: the same commands as ``gos voice ...``."""

import sys

from gregtec_voice.cli import main

sys.exit(main(sys.argv[1:]))
