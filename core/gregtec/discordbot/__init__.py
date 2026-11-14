"""The Discord bot (WO-9.1, plan 7.12, 4.2, 4.6; D39, D53, D220, D295-D297).

A process of its own, ``python -m gregtec.discordbot run --supervised``, which the supervisor
starts at login with the core (D77) and restarts with back-off. It talks to the core over HTTP
with the client token ``discord`` and to Discord through discord.py.

- :mod:`.handlers`: the commands, the Clayton-only gate, confirm buttons, job delivery
  (discord.py-free, so the tests drive it with fake interactions on a fake clock).
- :mod:`.adapter`: discord.py's client, command tree and buttons.
- :mod:`.core_api`: the core's routes it calls; :mod:`.texts`: what it says.
- :mod:`.settings`: ``[discord]`` in gos.toml; :mod:`.cli`: ``gos discord run|check``.

The durable job queue it uses lives in the core (:mod:`gregtec.remote.jobs`).
"""
