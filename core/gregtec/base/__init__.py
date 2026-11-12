"""My base (WO-3.8, plan 7.1 Request, D63, D64, D210-D212): what Clayton has, for the designer.

:mod:`.state` holds it (tier, owned machines, stock) and gives the planner its
:class:`~gregtec.base.state.MyBase` (the ``BaseInputs`` interface); :mod:`.routes` is the core's
API for it, which the dashboard's My base panel calls.
"""
