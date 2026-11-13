"""``python -m gregtec.watchers ...``: what gos-supervisor runs while Play (or Lab) plays (D259)."""

import argparse
import sys

from gregtec.watchers import runner

parser = argparse.ArgumentParser(prog="gregtec.watchers")
runner.add_arguments(parser)
sys.exit(runner.run(parser.parse_args()))
