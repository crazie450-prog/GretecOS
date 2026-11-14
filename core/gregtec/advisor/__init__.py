"""The next-step advisor and its proactive notices (WO-6.5, plan 7.5, D71, D266, D267).

- :mod:`.checks`: what is true of the base right now (a power deficit, a stalled machine, a
  plan buildable from stock, a quest that just opened up), read from the DB, every number cited;
- :mod:`.steps`: the advisor's ranked next steps, ranked by code (models explain, never reorder);
- :mod:`.notices`: the proactive notices, priority-gated with a 10-minute cooldown.
"""
