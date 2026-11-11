"""The agent runtime (plan section 4.4, Appendix B ``agents/``).

- ``tools/``: the tool layer both lanes call (WO-2.2).
- ``router``, ``prefetch``, ``fast_lane``, ``ask``: the router, instant commands, pre-retrieval,
  unsure-match questions and the Haiku fast lane behind ``POST /api/ask`` and ``gos ask``
  (WO-2.3, D150-D152); ``evals``: ``gos eval fast-lane`` (D153).
- ``deep_lane``: the deep lane's door (the ``DeepLane`` interface, the ``handoff`` tool and
  ``HandoffRecorder``, which only records, for the fast lane's eval and tests).
- ``deep``, ``deep_config``, ``hooks``: the deep lane itself (WO-2.4, D154-D156): a warm Claude
  Agent SDK session per task, its locked-down options and subagents, the ``PreToolUse`` tool
  gate and path guard, the per-task cap; ``deep_eval``: ``gos eval deep-lane`` (D157).
- ``cards``: the recipe card (WO-2.7, D172); ``recipe_eval`` (with ``recipe_eval_cli``):
  ``gos eval recipe-lookup``, the recipe-lookup skill's 50 questions scored against the DB
  (D171); ``recipe_oracle``: its fake models for both lanes (D172).

Personas arrive with WO-5.6.
"""
