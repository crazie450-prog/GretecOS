"""The pack-update playbook (WO-1.9, plan section 7.13, D44, D146-D149).

- :mod:`.releases`: the release watcher (GTNH stable releases, Play's addons) and the stock-pack
  check (D87).
- :mod:`.diff`: the recipe-, crop- and oredict-level diff of two export runs, grouped by mod, on
  top of WO-1.4's manifest diff (:mod:`gregtec.ingest.rundiff`).
- :mod:`.plans`: affected-plan flags over the plan format plans will have (Appendix D's ``plan``
  table: a plan cites ``r:`` and ``crop:`` keys and names its run).
- :mod:`.gtpin`: the GT pin check.
- :mod:`.playbook`: the update checklist ``gos pack update`` walks and records, and the "adopt?"
  decision, which only Clayton answers.
- :mod:`.playupdate`: the script Clayton runs from GOS_APP to update Play from the tested Lab.
"""
