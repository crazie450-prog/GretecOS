# Discord coexistence: the 1 hour session check (WO-5.5)

Clayton runs this on the gaming PC after the WO-5.5 PR is merged and deployed, and after `voice_client.md`, `stt.md`, `tts.md` (part E, ducking, included) and `wake.md`. About 1 h 20 min, most of it ordinary play with friends on Discord. Decisions: D58 (open mic, no Discord auto-mute), D56 (speech on the Arctis Chat endpoint), D246 (keys Discord users often bind), D247 (what was audited in code, and `gos voice doctor`).

**Acceptance (plan §8 WO-5.5):** Discord voice is unaffected during a 1 h session with GOS active. GOS never mutes, unmutes or reconfigures Discord (D58): your friends hear everything you say, the wake phrase and your questions included. What this checks is that GOS's microphone use, its speech, its chime and its key hook don't disturb Discord: no dropouts, no device switches, no mute or push-to-talk changes, no GOS audio in your friends' ears.

`gos` below means Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91). Run everything in a normal PowerShell window in your own account, never "Run as administrator". Play in **Lab** (`gos launch lab` from gos-dev, or by hand), never Play: nothing here touches Play, but the voice checks are done in Lab. `C:\Temp\gos55` below is only a folder for the notes you send back; any folder outside the game folders will do.

What GOS already guarantees in code (D247, tested in CI): the microphone and the output are opened in WASAPI **shared** mode, never exclusive; speech and the wake chime play on `[voice] output_endpoint` ("Chat"), never on Windows' default device, and GOS never changes Windows' default devices or Discord's; the key hook only listens (every key still reaches GTNH and Discord, nothing is suppressed or sent); nothing in the voice client opens, reads or changes Discord's process, files, settings or keybinds.

## A. Before the session (10 minutes)

1. Deploy: in the profile clone, `git pull`, then `uv run gos deploy`. GOS must be running (`gos status` shows the core `ok`).
2. A folder for the notes:
   ```powershell
   New-Item -ItemType Directory -Force C:\Temp\gos55 | Out-Null
   ```
3. **The doctor, before.** It prints and changes nothing:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice doctor | Tee-Object C:\Temp\gos55\doctor_before.txt
   ```
   Expect exit code 0 (`$LASTEXITCODE`) and no line starting with `!`. Check by eye:
   - `Capture` > `mode: WASAPI shared mode`, and `microphone:` is the Arctis Nova Elite's microphone;
   - `Output` > `speech and the wake chime:` is the Arctis **Chat** device, not Game and not speakers;
   - `Ducking` > `Windows communications ducking: 3 (do nothing)`. Anything else is marked `!`: run `tts.md` part E (`set_comm_ducking.ps1`), restart Discord and GTNH, and run the doctor again;
   - `Key hook` > `hook: installs and comes out again`;
   - `Keys`: the four keys. A `Discord WARNING` line under a key (D246: Pause, Scroll Lock and F13-F15) is a reminder for step 5, not a failure.

   A line marked `!` you can't clear: stop here and send the file (part D).
4. **The keys in GTNH**, with GTNH closed (it rewrites `options.txt` when it quits):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice keys lab
   ```
   Expect `free in GTNH` for all four keys, exit code 0.
5. **Discord, by hand** (GOS can't see Discord's settings, and never reads them). Open Discord, User Settings:
   - **Keybinds:** none of the four keys from step 3 is bound (Push to Talk, Push to Mute, Toggle Mute, Toggle Deafen, Toggle Voice Activity, Overlay and the rest). Check each `Discord WARNING` key twice. If one is bound, unbind it in Discord or give GOS another key (`voice_client.md` step 3).
   - **Voice & Video:** write down, in `C:\Temp\gos55\discord_before.txt` (or take a screenshot, `discord_before.png`): Input Device, Output Device, Input Mode (Voice Activity, as D58's open mic), the input sensitivity setting, Echo Cancellation, Noise Suppression (Krisp or Standard), Automatic Gain Control, and **Attenuation** (its percentage and the two "When I speak / When others speak" switches).
   - Input Device should be the Arctis microphone (or "Default"); Output Device the Arctis **Chat** device (or "Default"). Whatever they are, they must be the same after the session.
6. **Windows' devices.** Settings > System > Sound: write down the default Output and Input device (the doctor's `Windows' default output` and `Windows' default microphone` lines show them too).

## B. The session (1 hour)

7. Start Lab and load a world. Join a Discord voice channel with at least one friend, open mic (Voice Activity) as you play.
8. In a PowerShell window on monitor 2, start the voice client with the core:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice run
   ```
   Leave it running for the whole hour. Play as usual.
9. **Use GOS** about every 5 minutes, about 12 turns in all, mixing:
   - the wake phrase (`hey archivist`, or yours) and a question, e.g. "how do I make steel";
   - the talk key: hold it, ask, release;
   - a talk-key tap (under 0.3 s) while GOS speaks (barge-in);
   - confirm, cancel and next card once each, twice in the hour;
   - at least twice, a question **while a friend is talking**, and once while you are talking to friends and GOS answers.
10. **What to watch** during the hour. Note the time (clock, to the minute) of anything that happens:
    - **Discord's own indicators:** your avatar's green speaking ring lights when you talk and only then; no mute or deafen icon appears; no push-to-talk badge; the voice connection stays "Voice Connected" (no "RTC Connecting", no reconnect).
    - **Your friends' voices:** no dip in their volume when GOS speaks or listens (a dip is Windows ducking or Discord's attenuation, step 5), no dropouts, no robotic or garbled audio.
    - **GOS's own audio:** speech and the wake chime only in your headset, on the Chat side (the ChatMix dial balances GOS against the game), never on speakers.
    - **GTNH's sound:** no dip when GOS listens or speaks (Windows ducking).
    - **The GOS keys:** pressing them changes nothing in Discord (no mute, no push-to-talk) and nothing in GTNH.
11. At about 30 min and at the end, **ask a friend** (they hear everything, D58, so tell them beforehand you are testing):
    - Did my voice cut out, go quiet, or sound different at any time? When?
    - Did you ever hear a voice that wasn't mine (GOS's speech) or a two-tone chime?
    - Did I drop out of the channel or show as muted?
12. Stop the client with Ctrl+C in its window after the hour. Its log says, without audio, what it did: `logs\voice.log` in `%USERPROFILE%\GregtecOS`.

## C. After the session (5 minutes)

13. **The doctor, after**, and the difference:
    ```powershell
    & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice doctor | Tee-Object C:\Temp\gos55\doctor_after.txt
    Compare-Object (Get-Content C:\Temp\gos55\doctor_before.txt) (Get-Content C:\Temp\gos55\doctor_after.txt)
    ```
    Expect no output from `Compare-Object`: the same devices, ducking and hook as before.
14. **Discord and Windows again:** Discord's Voice & Video settings and Keybinds, and Windows' default Output and Input devices, are as in steps 5 and 6. Write them down the same way (`discord_after.txt` or `discord_after.png`).
15. **The turn timings** (for the record; WO-5.7 is the latency acceptance):
    ```powershell
    & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice bench | Tee-Object C:\Temp\gos55\bench.txt
    ```

## D. What to send back (paste into the WO-5.5 PR, or tell the session)

- `doctor_before.txt`, `doctor_after.txt` and the `Compare-Object` output (empty is the pass).
- Discord before and after (steps 5 and 14): the values or the screenshots, and whether anything changed.
- Windows' default devices before and after (steps 6 and 14).
- This table, filled in:

  | Check | Pass / fail | Notes (time, what happened) |
  |---|---|---|
  | No Discord dropout or reconnect in 1 h | | |
  | Your voice never cut out or changed (friend) | | |
  | Friends never heard GOS's speech or chime | | |
  | No mute, deafen or push-to-talk change in Discord | | |
  | Friends' voices never dipped when GOS spoke or listened | | |
  | GTNH's sound never dipped | | |
  | GOS speech and chime on the Chat endpoint only | | |
  | Discord's devices and settings unchanged after | | |
  | Windows' default devices unchanged after | | |
  | GOS answered: turns asked / turns answered | | |

- `bench.txt`, and anything odd with its time. `voice.log` holds no audio; send the lines around an odd time if asked.

A fail with its time and what you were doing is what lets the next session find the cause: the doctor's lines, Discord's attenuation and Windows ducking are the first suspects for dips; a dropout at the moment GOS opened its microphone or speech points at the device sharing (D247).
