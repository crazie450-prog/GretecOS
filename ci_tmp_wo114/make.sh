#!/bin/bash
# WO-11.4 TEMPORARY: copies of test_the_designer_job_api that print the median pair.
set -e
d=tests/unit/planner; src=$d/test_design_job_api.py
pr='            open("pairs.log", "a", encoding="utf-8").write("PAIRS %.3f %s\\n" % (statistics.median(pairs), [round(p, 3) for p in pairs]))'
mk() { sed -e "s|^            assert statistics.median(pairs) < 0.5, pairs|$pr\n&|" "$@"; }
mk $src > $d/test_zz_busy_design_job_api.py
mk -e 's|                    sum(i \* i for i in range(2000))|                    time.sleep(0.01)|' $src > $d/test_zz_idle_design_job_api.py
mk -e 's|^                end = time.monotonic() + 60.0|                sys.setswitchinterval(0.05)\n&|' $src > $d/test_zz_mut_design_job_api.py
grep -c PAIRS $d/test_zz_*_design_job_api.py
grep -c "time.sleep(0.01)" $d/test_zz_idle_design_job_api.py
grep -c setswitchinterval $d/test_zz_mut_design_job_api.py
