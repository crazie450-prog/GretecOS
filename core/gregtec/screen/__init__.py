"""Screen reading (plan 7.7, WO-8.2, D288, D289): one frame per press of the screen key.

The voice client captures the game window when Clayton presses the screen key and posts that
one frame to ``POST /api/screen`` (:mod:`.routes`). The core keeps it for 24 h
(:mod:`.cache`), adds the game-state snapshot (:mod:`.snapshot`) and asks the vision model
through the fast lane's loop (:mod:`.lane`), which answers with a short spoken line and a card
(:mod:`.reader`). Nothing in the core can capture the screen: no model tool does, and no code
here imports a capture library. ``gos eval screen`` (:mod:`.eval`) scores five scripted cases.
"""
