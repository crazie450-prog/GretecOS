# Voice client: keys, microphone and acceptance checks (WO-5.1)

Clayton runs these on the gaming PC after the WO-5.1 PR is merged and deployed. They take about 30 minutes. Decisions: D179 (the client and its core routes), D180 (talk key, hotkeys, hook and watchdog), D181 (capture, pre-roll, timings).

`gos` below means Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91); the voice client is part of it (the `voice` dependency group, D179). Run everything in a normal PowerShell window, never "Run as administrator": a hook in an elevated window would see a different set of windows than the game's. Speech-to-text (WO-5.2), text-to-speech (WO-5.3) and the wake word (WO-5.4) aren't in this WO: until they land, the client records segments without sending them, prints what it would say, and listens to the talk key only.

## A. Setup (once)

1. Deploy: in the profile clone, `git pull`, then `uv run gos deploy`. GOS must be running (`gos status` shows the core `ok`).
2. A token for the voice client, stored in your keyring as `gregtec-os/token-voice` (D117):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe token new voice
   ```
3. **The keys.** The defaults are talk F13 (hold), confirm F14, cancel F15, next card F16. GTNH and Discord bind none of F13-F24 by default, and nothing is suppressed, so the keys must stay unbound in both (QA:V8). Most keyboards have no F13-F16: map a mouse side button or a macro key to them in SteelSeries GG (or the mouse's own software), or pick other keys in `%USERPROFILE%\GregtecOS\config\gos.toml`:
   ```toml
   [voice]
   talk_key = "F13"          # one key: F13-F24, Pause or Scroll Lock
   talk_mode = "hold"        # or "toggle": press to start, press again to send
   confirm_key = "F14"
   cancel_key = "F15"
   next_card_key = "F16"
   input_device = ""         # "" = Windows' default microphone; else part of a name from step 4
   ```
   Only F13-F24, Pause and Scroll Lock are allowed: they type and edit nothing, and neither GTNH nor Discord binds them by default. F1-F12 (Minecraft hard-codes F1 and F3), letters, digits, Space, Enter, Insert, Delete, Home, End, Page Up, Page Down, the arrows, the numpad, Menu, modifiers, mouse buttons themselves and combinations are refused (D180). A wrong key makes every `gos voice` command say why.
4. **The microphone.**
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice devices
   ```
   One line per WASAPI microphone; `[default, used]` marks the one the client opens. If `used` isn't the Arctis Nova Elite's microphone, set `input_device = "Arctis"` (any part of its name that matches only it) and run it again.
5. **GTNH's bindings.** Close GTNH first (it rewrites `options.txt` when it quits), then:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice keys play
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice keys lab
   ```
   Expect `free in GTNH` for all four keys and exit code 0. `BOUND in GTNH to key.xyz` means a GTNH control uses that key (NEI's show as `nei.options.keys...`: GTNH's NEI keeps its keys in `options.txt` too): unbind it in GTNH's Controls menu, or pick another key in step 3. It reads `options.txt` once, read only, the way GOS reads game files. For F20-F24 it says GTNH's Java 8 build can't bind them; Play runs the Java 17+ build (D86), so check those in the Controls menu by hand.
6. **Discord's keybinds** live where GOS can't read them: open Discord, User Settings > Keybinds, and check that none of the four keys is there (Push to Talk, Push to Mute, Toggle Mute and the rest).

## B. Acceptance (§8 WO-5.1): the keys work with GTNH focused, fullscreen and borderless, and trigger nothing in GTNH or Discord

7. Start Lab and load a world (all testing happens in Lab: `gos launch lab` from gos-dev, or by hand). Join a Discord voice channel with open mic.
8. In a PowerShell window on monitor 2:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice test --seconds 600
   ```
   It prints the keys, then one line per key press and release the hook sees, the state changes, and one line per recorded segment (`segment 1: 2.34 s (300 ms pre-roll), peak -18 dBFS`). Nothing is sent anywhere. `--save C:\Temp\voice` also writes each segment as a WAV, so you can hear the first syllable is there (it is your voice: delete them afterwards).
9. **Borderless.** Lab has the §5.6 tweak `window.borderless=true` in `config\lwjgl3ify.cfg` (`check_tweaks.py` reports it), so F11 in GTNH gives borderless fullscreen. Click into GTNH, press F11, then, with GTNH focused:
   - hold the talk key for about 2 s while saying something, release;
   - tap the talk key once quickly (under 0.3 s: that only stops speech, so no segment);
   - press confirm, cancel and next card once each;
   - open GTNH's chat (T), press all four keys, close the chat.

   Pass: the window on monitor 2 lists every press and release with `(hook N ms)` and no `(the hook missed it ...)`; the 2 s press gives a segment of about 2.4 s that isn't `silent`; confirm and cancel show `-> would ask the core: 'confirm'` / `'cancel'`, next card `-> would tell the dashboards: next_card`. In GTNH nothing happened (no screen opened, no action, nothing typed in chat); in Discord no mute, deafen or push-to-talk indicator changed.
10. **Exclusive fullscreen.** Quit GTNH, set `window.borderless=false` in Lab's `config\lwjgl3ify.cfg`, start Lab, press F11 and repeat step 9. Then set it back to `true` (the next `sync_instances.py` run would also copy Play's back).
11. Stop the test with Ctrl+C (or let `--seconds` run out). The summary must show the presses of all four keys, `blocks dropped: 0`, and `watchdog: 0 re-installs`. A non-zero watchdog count is not a failure (it means the hook was lost and put back, as designed), but note it in the PR with what you were doing at the time.

## C. The client with the core

12. With GOS running:
    ```powershell
    & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice run --transcript "how do I make steel"
    ```
    `--transcript` stands in for speech-to-text until WO-5.2: every segment "says" that text. Hold the talk key 1 s and release: the window prints `GOS: Steel Ingot: ...` (the fast lane's checked answer). Press confirm: `GOS: Nothing is waiting for confirmation.` Press cancel: nothing is said (nothing was waiting, and cancel never makes GOS talk then). Press next card: nothing is said (the dashboard, WO-2.6, moves to its next card).
13. **Core offline.** In another window `gos stop`; within about 20 s the voice window prints `GOS: Core offline.` once. `gos serve`: the client reconnects by itself (the next talk-key press is answered again).
14. **Microphone lost.** With the client still running, unplug the headset (or disable its microphone under Windows Settings > System > Sound > Input) for about 5 s: the window prints `GOS: Microphone lost.` once. Plug it back in (or enable it): within about 10 s the client opens it again by itself; a 2 s talk-key press is answered again. Stop the client with Ctrl+C.
15. **Latency so far** (PM14 needs speech-to-text and text-to-speech, WO-5.2 and WO-5.3; WO-5.7 measures it in full): after about 20 talk-key turns in step 12,
    ```powershell
    & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice bench
    ```
    prints P50 and P90 per stage from `logs\voice.log`: `close` (key up to segment closed: expect about 120-160 ms, the post-roll), `core` (the ask round trip, the fast lane's first token included) and, once text-to-speech exists, `total` (key up to first audio) with PM14's verdict (talk key P50 <= 1.7 s, wake word <= 2.2 s). Paste the output into the PR.
