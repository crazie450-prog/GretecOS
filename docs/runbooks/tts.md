# Text-to-speech: engines, the latency bench, the listening check, the Chat endpoint and ducking (WO-5.3)

Clayton runs these on the gaming PC after the WO-5.3 PR is merged and deployed, and after `stt.md` (speech-to-text) and part A of `voice_client.md`. About 30 minutes. Decisions: D203 (engines, models, licences), D204 (the speech filter, the lexicon, sentence streaming, the bench and the check), D205 (the output device, barge-in, Windows ducking).

`gos` below means Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91). Run everything in a normal PowerShell window in Clayton's own account. Models download once into `%USERPROFILE%\GregtecOS\models\kokoro` (about 350 MB) and `models\piper` (about 63 MB) at pinned SHA-256s. Nothing here touches Play, Lab or Export.

What is measured here (plan 8 WO-5.3): first audio <= 400 ms after the first sentence; 30 GTNH terms and 10 number/unit phrases spoken correctly (by ear); no citation or id is ever spoken. The build container could only run the bench on a shared CPU and a synthetic round trip (D204), so this run is the acceptance.

## A. Setup (once)

1. Deploy: in the profile clone, `git pull`, then `uv run gos deploy`. The release now also holds the `tts` dependency group (kokoro-onnx, which brings ONNX Runtime, phonemizer and espeak-ng; D203).
2. Which device speech will play on:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice devices
   ```
   Under `Output (output_endpoint = 'Chat')` the Arctis Nova Elite's Chat endpoint must be marked `[used]`. If a line `(no output device's name contains 'Chat' ...)` or `('Chat' is in 2 device names ...)` shows, set the right device's whole name (an exact name wins over other matches) or a longer part of it in `%USERPROFILE%\GregtecOS\config\gos.toml`:
   ```toml
   [voice]
   output_endpoint = "Chat (Arctis Nova Elite)"   # a part of the device name above; "" = Windows' default
   ```
   Paste the output into the PR (it closes the device-name part of V11).

## B. The latency bench (CPU, then GPU)

3. With GTNH running as usual (it competes for the CPU and GPU, as it will in play):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice tts-bench
   ```
   It says ten sample answers with Piper and Kokoro on the CPU (and ElevenLabs if its key is stored, step 6) through the headset's Chat endpoint, and prints per engine `P50 ... ms, P90 ..., load ... ms: meets/misses the target`, then `Latency pick: ...` with the two `gos.toml` lines, and a TSV report path. The latency pick only says which engine is fast enough; it does not say the voice is understandable (in the build container Piper was the latency pick but the less intelligible voice, D204), so step 5 also needs step 7. You will hear the samples (placeholder answers, not game data). Paste the output into the PR.
4. **GPU (optional; only if no CPU engine met the target, or Kokoro's voice is wanted and missed it).** ONNX Runtime's GPU build replaces the CPU one, so try it in a throwaway clone, never in the release:
   ```powershell
   git clone https://github.com/crazie450-prog/GretecOS.git $env:TEMP\gos-tts-gpu
   cd $env:TEMP\gos-tts-gpu
   uv sync --group tts --group stt
   uv pip uninstall onnxruntime
   uv pip install "onnxruntime-gpu[cuda,cudnn]==1.31.1"
   uv run --no-sync gos voice tts-bench --engine kokoro-gpu --engine piper-gpu
   ```
   The bench prints `VRAM +... MB` per GPU engine (from `nvidia-smi`). If it says `ONNX Runtime has no GPU provider here`, try `uv pip uninstall onnxruntime-gpu` then `uv pip install onnxruntime-directml==1.24.4` in its place (any GPU, through DirectML). Paste the output; delete the folder afterwards. Running on the GPU in play would need a follow-up change to the release (D203).
5. Set the latency pick in `gos.toml` (`tts = "piper"` or `"kokoro"`, `tts_device = "cpu"`) only if step 7 also passes with it. Latency alone does not decide. Kokoro on the CPU took 1.3 to 3.5 s to first audio in the build container, which would also break PM14's 1.7 s talk-key target, so keep it only if the bench here says it meets 400 ms. A `gpu` pick cannot be set in the release yet (step 4, D203): note it in the PR instead. Appendix C's default stays `kokoro` on the CPU until this run decides.

## C. ElevenLabs (optional, paid)

6. Only if Clayton wants the cloud voice (the text of each answer goes to ElevenLabs): make an API key on elevenlabs.io, then
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe key set elevenlabs
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice tts-bench --engine elevenlabs
   ```
   The default voice is a premade one (Adam); another voice id goes in `[voice] tts_voice`. With `tts = "elevenlabs"`, a failure (no network, no credit) falls back to Piper, then Kokoro, for that sentence.

## D. The listening check (acceptance: 30 terms and 10 number/unit phrases)

7. ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice say --phrases
   ```
   It prints each of the 40 rows of `tests/evals/tts_phrases.tsv` as said (`t07: You need L U V for this.`) and says it on the Chat endpoint. Listen: each term must be the word you say in play (LuV as "L U V", EBF as "E B F", naquadah as "nah-kwa-dah", ...), each number and unit natural ("nineteen-twenty E U per tick"). Note the ids of any that sound wrong in the PR; a pronunciation is fixed in the vault's `glossary.tsv` `say` column (no code change). Repeat with `--tts piper` if Piper is the pick.
8. Optional, a second opinion from speech-to-text on the same audio (needs the `stt` models from `stt.md`):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice tts-check
   ```

## E. Windows communications ducking: "Do nothing"

Windows turns other sounds down by 80% while it sees a "communication session" (a headset's Chat endpoint or microphone in use) [D§2.6], so GTNH would dip whenever GOS listens or speaks. This sets Sound > Communications to "Do nothing" for Clayton's account only (`HKCU\Software\Microsoft\Multimedia\Audio\UserDuckingPreference` = 3). It is Clayton's own setting, so Clayton runs it:

9. ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File C:\GregtecOS-app\current\scripts\set_comm_ducking.ps1
   ```
   It prints what it was and what it is now, and keeps the old value in `%USERPROFILE%\GregtecOS\data\comm_ducking_before.txt` (under `%GOS_HOME%` instead when that is set). Restart Discord and GTNH (or sign out and in). Check: Control Panel > Sound > Communications shows "Do nothing".
   - Show only: add `-Show`.
   - **Undo**: add `-Undo` (puts back the value it found; with none saved it removes the value, which is Windows' default, "Reduce the volume of other sounds by 80%"). Or by hand: Sound > Communications > "Reduce the volume of other sounds by 80%".

## F. In play

10. `gos voice run` (as in `voice_client.md`): ask a question; the answer plays on the Chat endpoint, the ChatMix dial balances it against the game, and pressing the talk key (or speaking, with the wake word on) stops it at once. `gos voice bench` then shows `speak_ms` (reply to first audio) and `total_ms` per turn against PM14.
