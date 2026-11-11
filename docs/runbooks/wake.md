# The wake word: the phrase, false wakes and hits (WO-5.4)

Clayton runs these on the gaming PC after the WO-5.4 PR is merged and deployed, after `stt.md` (speech-to-text works, ideally on the GPU) and part A of `voice_client.md`. Decisions: D185 (the engine: stages, chime, cancel window, end of speech, barge-in by voice), D186 (the phrase, thresholds and the second stage's match), D187 (the phrase harness and what it found on synthetic speech).

`gos` below means Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91). Run everything in a normal PowerShell window. Everything here reads and writes only under `%USERPROFILE%\GregtecOS` (`models\kws`, `data\evals\wake`, `data\reports`, `logs`). Nothing touches Play, Lab or Export.

What is measured here (plan 8, WO-5.4): with Discord's open mic and GTNH running, **at most 1 false wake an hour over 2 hours** of play, **at least 38 of 40 wake attempts** succeed (the question captured, not just a chime), and the wake path's latency meets PM14 (P50 at most 2.2 s from the end of your question to GOS's first audio; that last one needs text-to-speech, WO-5.3, and is WO-5.7's benchmark). The build container could only score the candidate phrases on synthetic voices and generated noise (D187), so this run is the acceptance, and it also picks the phrase (PM08).

## A. Setup (once)

1. Deploy: in the profile clone, `git pull`, then `uv run gos deploy`. The release now also holds the wake-word engine (the `wake` dependency group: sherpa-onnx and sentencepiece, about 30 MB; D185).
2. The phrase, in `%USERPROFILE%\GregtecOS\config\gos.toml` (the defaults shown; leave them out to keep them):
   ```toml
   [voice]
   wake_engine = "sherpa-onnx"     # or "openwakeword" with a model you trained (part E)
   wake_phrase = "hey archivist"   # 1-4 words; never "GregTech" or "Gregtec" (PM08)
   wake_threshold = 0              # 0 = the tuned default; higher wakes less often
   ```
   The candidates, with what the harness found for each, are in the PR and in D187. The second stage can't tell "hey archivist" from a sentence about "the archivist": if you call GOS that when talking to friends, pick "hello quartermaster". Any phrase works with sherpa-onnx without training; `gos voice` refuses one with "GregTech"/"Gregtec" in it, or one too short to be safe.
   The first `wake-eval` or `voice run` downloads the wake-word model (about 18 MB, once, into `models\kws`).

## B. Record 40 wake attempts (about 10 minutes)

3. Wear the headset as you do when playing, GTNH running, Discord open. Each attempt is the phrase and a short question, said as you would to GOS ("Hey archivist, what uses tin?"). Record them with the talk key as the "microphone switch" (`gos voice test --save` keeps each talk-key segment as a WAV):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice test --save "$env:USERPROFILE\GregtecOS\data\evals\wake\wake"
   ```
   Press and hold the talk key, wait half a second, say the attempt, wait half a second, release (the harness adds 2 s of silence after each recording, so end of speech can fire). Do it 40 times with varied questions and the way you really talk (some quick, some tired, some over game noise). Ctrl+C when done; do all 40 in one run (a new run numbers its files from `segment-1.wav` again and overwrites them). Keep a slip of the key: it is an attempt like any other.
4. These are your voice: they stay in `%USERPROFILE%\GregtecOS\data\evals\wake\` and are never committed or sent anywhere.

## C. Record 2 hours of open-mic play

5. Start a normal session: GTNH, Discord open mic with friends, game sound as usual. Then, in a PowerShell window:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice capture "$env:USERPROFILE\GregtecOS\data\evals\wake\play\session-1.wav" --minutes 120
   ```
   It records your microphone (16 kHz mono, about 230 MB for 2 hours) and stops by itself. Play as usual; say the wake phrase only if you'd naturally talk about GOS (that counts as a false wake if it isn't meant for it, which is the point). This is your voice and your friends' names: keep it in GOS_HOME, never share it, delete it after the PR.
   `gos voice capture` and `gos voice run` both open the microphone; WASAPI shared mode lets them run together, but there is no need: the capture is scored offline in part D.

## D. Acceptance (plan 8 WO-5.4)

6. With the stt engine on the GPU (`stt.md`), score the configured phrase on the recordings, both stages:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice wake-eval --audio "$env:USERPROFILE\GregtecOS\data\evals\wake"
   ```
   It prints one table row (`stage-1`, `chimed` and `asked` out of 40: detected, passed the second stage, and question captured, i.e. the wake ended in end of speech with no cancel; the median detection latency after the phrase, minutes of play, stage-1 and final false wakes, false wakes per hour), then a `missed` line per attempt whose question wasn't captured, with why (`not detected`, `rejected`, `no question`, `no end of speech`, `woke twice`), and a `false wake` line per WAV that woke GOS, and writes `data\reports\wake-eval-<time>.tsv`.
   **Pass:** exit code 0, i.e. `asked` at least 38/40 and `per h` at most 1.0 (over at least 120 minutes). Paste the table and every `missed`/`false wake` line into the PR.
7. If it is NOT met, try in this order and paste each table (nothing is changed by hand in the code):
   - Too many false wakes: `--threshold 0.35`, then `0.45` (stage 1 stricter); the stage-2 column shows how many the second stage already removes.
   - Too many misses marked `not detected`: `--threshold 0.1`, then `0.05` (the default is 0.15, D186). Misses marked `rejected` are the second stage's: paste them, a threshold won't help.
   - Another candidate on the same recordings needs its own 40 attempts (part B with that phrase), but the play recording is reused: `--phrase "hello quartermaster"` (the runner-up in D187; "okay tessera" and "hey tessera" aren't heard by sherpa-onnx at all).
   The threshold that passes goes into `gos.toml` as `wake_threshold`.
8. Stage 1 alone, for the record (how much the second stage does on your voice): add `--no-verify` and paste the row.

## E. Live, with the voice client

9. With GOS running and the voice token stored (`voice_client.md` step 2):
    ```powershell
    & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice run
    ```
    It prints `Loading speech-to-text...`, `Loading the wake word...`, then `Voice client: ...; speech-to-text: ...; wake phrase "hey archivist" (sherpa-onnx). Ctrl+C stops it.` Say "Hey archivist" and a question: a short two-tone chime in the headset means GOS heard the phrase (after the second stage). Start the question within 1.5 s of the chime; you can also run straight on from the phrase without waiting for it. A pause of about 0.6 s ends the question; the answer is printed (spoken once WO-5.3 lands). If you say nothing after the chime, the wake is dropped silently; the cancel hotkey drops it too. The talk key keeps working as the backup. If you say the phrase while holding the talk key, the chime may still play: the talk key's segment wins and the wake is ignored (D185).
10. Barge-in by voice (D180, D185): while GOS is speaking (WO-5.3), talk for a third of a second: it stops.
11. After a session, `gos voice bench` starts with `wake word: N detections, M chimed (... asked, ... no question, ... rejected)`. Count the chimes you didn't mean: over 2 hours, at most 2 (plan 8). The log holds no words, only each wake's outcome and timings.

## F. The openWakeWord alternative (only if sherpa-onnx can't meet the targets)

12. Train a model for the phrase with livekit-wakeword or openWakeWord's notebook ([H§1.2]; on WSL2 or a cloud GPU, an afternoon), export it as ONNX, copy it to `%USERPROFILE%\GregtecOS\models\openwakeword\<phrase>.onnx`, and set:
    ```toml
    [voice]
    wake_engine = "openwakeword"
    wake_model = 'C:\Users\<you>\GregtecOS\models\openwakeword\hey_archivist.onnx'
    ```
    GOS runs it on ONNX Runtime, never tflite (the Windows trap, [H§1.1]), and downloads openWakeWord's two feature models (about 2.4 MB) once. Then parts D and E with `--wake-engine openwakeword`.

## For developers: the harness on synthetic speech, and the slow tests (no PC needed)

- `uv sync --group stt --group wake` installs the engines (CI and the default sync leave them out; unit tests use fakes).
- `gos voice wake-eval --kokoro DIR` scores every candidate of `tests/evals/wake_phrases.tsv`: Kokoro (kokoro-onnx, `uv pip install kokoro-onnx`, not a dependency; its `kokoro-v1.0.onnx` and `voices-v1.0.bin` in DIR) says each phrase 40 times over generated noise, against the open-mic lines of `tests/evals/wake_chatter.txt` and the near misses, plus `--noise-minutes` of noise. Its second stage uses the configured speech-to-text; on a PC without a GPU add `--engine whisper-cpu --model base.en` (or `--no-verify` for stage 1 alone). Nothing but the report is written; never commit audio, models or reports.
- `tests/integration/test_wake_models.py` (marked `slow`) runs the real KWS engine, Silero VAD, Whisper and openWakeWord on ONNX; it is skipped unless `GOS_WAKE_TEST_HOME` (with `models/kws`), and for the spoken checks `GOS_WAKE_TEST_KOKORO` and `GOS_WAKE_TEST_OWW`, are set (its docstring says what each holds).
