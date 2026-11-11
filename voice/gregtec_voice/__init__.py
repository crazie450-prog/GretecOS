"""Gregtec OS voice client (plan 4.1, Appendix B; WO-5.1, D179-D181).

| module      | what                                                                  |
|-------------|-----------------------------------------------------------------------|
| ``audio``   | WASAPI capture at 16 kHz mono, the 300 ms pre-roll, talk segments     |
| ``ptt``     | the low-level key hook that only enqueues, its watchdog, the talk key |
| ``keys``    | key names and the check against GTNH's bindings                       |
| ``client``  | the controller, the core connection and the process                   |
| ``hooks``   | where STT (WO-5.2), TTS (WO-5.3) and the wake word (WO-5.4) plug in   |
| ``settings``| gos.toml's ``[voice]`` keys                                           |
| ``cli``     | ``gos voice run``, ``test``, ``keys``, ``devices``, ``bench``         |
"""
