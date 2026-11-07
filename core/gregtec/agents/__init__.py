"""The agent runtime (plan section 4.4, Appendix B ``agents/``).

- ``tools/``: the tool layer both lanes call (WO-2.2).
- ``router``, ``prefetch``, ``fast_lane``, ``ask``: the router, instant commands, pre-retrieval,
  unsure-match questions and the Haiku fast lane behind ``POST /api/ask`` and ``gos ask``
  (WO-2.3, D150-D152); ``evals``: ``gos eval fast-lane`` (D153).
- ``deep_lane``: the deep lane's interface, a recording stub until WO-2.4.

Hooks and personas arrive with WO-2.4 and WO-5.6.
"""
