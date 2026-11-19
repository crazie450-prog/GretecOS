#!/bin/bash
# WO-11.4 TEMPORARY: repeat the CI repeat offenders under load, before (test_zz_before_*) and after.
# usage: run.sh <reps> <label> <pytest args...>; two busy loops run beside it.
reps=$1; label=$2; shift 2
uv run --locked python -c "while True: pass" & a=$!
uv run --locked python -c "while True: pass" & b=$!
set +e
PYTHONPATH=ci_tmp_wo114 uv run --locked pytest -p repeat_plugin --rep "$reps" -p no:cacheprovider -q -rf "$@" > "out-$label.txt" 2>&1
rc=$?
kill $a $b 2>/dev/null
echo "=== $label (exit $rc)"
grep -E "^(FAILED|ERROR) " "out-$label.txt" | sed -E 's/\[[0-9]+\]//' | cut -c1-220 | sort | uniq -c
tail -1 "out-$label.txt"
exit 0
