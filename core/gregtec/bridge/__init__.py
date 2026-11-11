"""The OpenComputers bridge, the core's side (plan 7.9; contract docs/contracts/oc_bridge.md).

- :mod:`gregtec.bridge.ingest`: what the core does with one telemetry message and the reply it
  sends, whichever transport carried it (WO-7.3 defines it; WO-7.1's HTTP endpoint adopts it).
- :mod:`gregtec.bridge.mailbox`: M1, the file mailbox on an OC computer's managed drive (WO-7.3).
"""
