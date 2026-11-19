#!/bin/bash
# WO-11.4 TEMPORARY: run.sh <reps> <label> <load 0|1> <pytest args...>; prints the medians.
reps=$1; label=$2; load=$3; shift 3
if [ "$load" = 1 ]; then
  uv run --locked python -c "while True: pass" & a=$!
  uv run --locked python -c "while True: pass" & b=$!
fi
PYTHONPATH=ci_tmp_wo114 uv run --locked pytest -p repeat_plugin --rep "$reps" -p no:cacheprovider -q -s -rf "$@" > "out-$label.txt" 2>&1
rc=$?
[ "$load" = 1 ] && kill $a $b 2>/dev/null
echo "=== $label (exit $rc)"
grep -oE "PAIRS [0-9.]+" "out-$label.txt" | awk '{print $2}' | sort -n | awk '{v[NR]=$1} END {if (NR) printf "medians n=%d min=%s p50=%s p90=%s max=%s\n", NR, v[1], v[int((NR+1)/2)], v[int(NR*0.9+0.5)], v[NR]}'
grep -E "^(FAILED|ERROR) " "out-$label.txt" | sed -E 's/\[[0-9]+\]//' | cut -c1-200 | sort | uniq -c
tail -1 "out-$label.txt"
exit 0
