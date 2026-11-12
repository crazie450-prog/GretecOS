# Voice benchmarks with GTNH running (WO-5.7)

Clayton runs these on the gaming PC after the WO-5.7 PR is merged and deployed, after `voice_client.md`, `stt.md`, `tts.md` and `wake.md` (both triggers work and GOS speaks). It takes about 45 minutes. Decisions: D248 (the harness, what it measures and how, the spoken-length count), D249 (FPS and TPS: where they come from and what counts as impact).

`gos` below means Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91). Run everything in a normal PowerShell window, as yourself.

**GTNH-Lab only.** Every game step uses **GTNH-Lab**, never Play: GOS adds nothing to the game (no mod, no injected command); you read F3 and type `/forge tps` yourself, and the harness reads a *copy* of Lab's log. The commands write only under `%USERPROFILE%\GregtecOS` (`data\reports`) and the scratch folder `C:\Temp\gos57`.

What is checked, by code (plan 8 WO-5.7), with GTNH running:

| check | target |
|---|---|
| latency, talk key (PM14) | P50 at most 1.7 s from key release to GOS's first audio |
| latency, wake word (PM14) | P50 at most 2.2 s from the end of your question to the first audio |
| RAM free at peak (QA:A7) | at least 4 GB (4096 MB) free at the lowest point while GOS runs |
| FPS (F3) | the drop with GOS running is at most 10% |
| TPS (`/forge tps`) | no drop: at most 0.1 TPS (D249) |
| spoken length (D23), eval set | at least 95% of the eval set's answers (the fake model) are heard within 2 sentences and 40 words, and the speech filter had to cut at most 5% of them |
| spoken length (D23), live | the same two, on the answers GOS actually spoke in part C (the real model): each turn logs how many sentences and words it was heard as, and whether the filter cut it; counts only, never the words |

It also records each stage's P50 and P90 (key held, segment close, speech-to-text, core, speech), the peak RAM of the core and the voice client, and VRAM used at peak (with nvidia-smi; plan's L7 wants at least 1 GB of VRAM left).

## A. Setup (once)

1. Deploy: in the profile clone, `git pull`, then `uv run gos deploy`.
2. A scratch folder for the readings: `New-Item -ItemType Directory -Force C:\Temp\gos57`.
3. Lab's log folder, for the copies below (Lab's game folder is `.minecraft` or `minecraft`, whichever `gos voice keys lab` found):
   ```powershell
   $lab = 'C:\Games\PrismLauncher\instances\GTNH-Lab\.minecraft'
   ```
4. Lab needs commands for `/forge tps`: in the Lab world, **Esc > Open to LAN > Allow Cheats: ON > Start LAN World** (for this session only; it changes nothing in Play).

## B. Baseline: GTNH-Lab without GOS (10 minutes)

5. Stop GOS: `gos stop` (the core and anything the supervisor runs), and close any `gos voice run` window. Leave open what you normally have while playing: Discord, the browser, Obsidian.
6. Launch **GTNH-Lab**, open the world you use for Lab tests (`lab-ops` is fine), stand at one spot you will come back to (your base, facing the same way), and wait 2 minutes for chunks and the JIT to settle.
7. **FPS:** press F3. Its top-left line starts with `N fps`. Read it five times, ten seconds apart, and type the five lines into `C:\Temp\gos57\fps-before.txt`, e.g.:
   ```
   118 fps
   121 fps
   117 fps
   120 fps
   119 fps
   ```
8. **TPS:** in chat, `/forge tps`. Wait 30 s and run it again (the second reading counts). Then copy Lab's log, never move or edit it:
   ```powershell
   Copy-Item "$lab\logs\latest.log" C:\Temp\gos57\tps-before.log
   ```
   (Chat goes into `latest.log` as `[CHAT] Overall : Mean tick time: ... ms. Mean TPS: ...`. If your copy has no such line, type the `Overall` line into `C:\Temp\gos57\tps-before.txt` instead and use that file below.)
9. Leave Lab running.

## C. With GOS running (25 minutes)

10. Start GOS: `gos serve` (or let the login supervisor run it), open the dashboard on monitor 2 as usual (it switches to glance when GTNH has focus), and start the voice client in its own window:
    ```powershell
    & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice run
    ```
11. In another window, start the benchmark. It samples RAM every second and VRAM every 5 s for 20 minutes and then reads the turns the voice client logged meanwhile, so start it first and then play:
    ```powershell
    & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice benchmark --minutes 20 `
        --fps-before C:\Temp\gos57\fps-before.txt --fps-during C:\Temp\gos57\fps-during.txt `
        --tps-before C:\Temp\gos57\tps-before.log --tps-during C:\Temp\gos57\tps-during.log `
        --out C:\Temp\gos57\bench.tsv
    ```
    It prints `Sampling for 20 minutes (core: <pid>, voice: <pid>)`. If a pid says `not running`, the core or the voice client isn't up: Ctrl+C, fix it, start again. (`--pid core=1234` names a process it didn't find.) The two `-during` files don't exist yet; you write them in steps 13 and 14, before the 20 minutes are up.
12. **Questions:** back at the same spot in Lab, ask **20 questions with the talk key and 20 with the wake word**, spread over the 20 minutes, as you would while playing (any lookups: "how do I make steel", "what uses tin", "what's the recipe for a vacuum tube", quests). Wait for each answer to finish before the next.
13. **FPS** around minute 10, at the same spot and facing: F3 five times, ten seconds apart, into `C:\Temp\gos57\fps-during.txt`. Take at least one reading while GOS is speaking.
14. **TPS** around minute 15: `/forge tps` twice, 30 s apart, then `Copy-Item "$lab\logs\latest.log" C:\Temp\gos57\tps-during.log`. Check that the chat showed the `Overall` reply both times: if the command was mistyped or not logged, the copy still ends with step 8's reading, and the benchmark reports TPS `not measured` ("no new /forge tps reading") rather than passing it.
15. When the 20 minutes are up (or Ctrl+C after step 14 and your 40 questions), it prints each stage per trigger, then runs the eval set's 60 questions on the fake model (a few seconds, no spending), then the summary, and writes `C:\Temp\gos57\bench.tsv` and `C:\Temp\gos57\bench.txt`. A second Ctrl+C during the eval set still writes the report, with the eval set `not measured (interrupted)`.

## D. Acceptance (plan 8 WO-5.7)

16. **Pass:** the summary ends `Result: PASS` (exit code 0): both PM14 lines `pass`, `RAM free at peak` at least 4096 MB, `FPS drop` at most 10%, `TPS` within 0.1, and both `Spoken length` lines (eval set and live) at least 95% within D23 and at most 5% cut by the filter. `INCOMPLETE` means something wasn't measured; its `not measured:` line says what (usually a missing `-during` file or no turns for one trigger: ask more questions and run step 11 again).
17. Paste `bench.txt` into the PR, with the `voice-benchmark` TSV attached if a check failed. The files hold timings and counts only, never what you said.
18. If a check FAILs, paste it and try, one at a time, re-running part C each time:
    - PM14 missed: the stage lines show where the time goes. `stt` large: `stt.md` (GPU, model). `core` large: the fast lane (`gos eval fast-lane` on the real key). `speak` large: `gos voice tts-bench` (`tts.md`) and the engine it picks. `close` on the wake path includes about 0.6 s of end-of-speech detection (PM14 allows for it).
    - RAM free under 4 GB: close what you can (a second GTNH, browser tabs) and note what was open; QA:A7's fixes are Export only when stale and no Play launch while Export runs.
    - Spoken length (live) cut more than 5%: the real model writes longer answers than D23 allows and the filter shortens them, which can drop the point of an answer. Paste the line; the fix is the fast lane's prompt rules, in a follow-up WO (changing them is not a step for you).
    - FPS or TPS dropped: run part C again with `[voice] stt_engine` on the CPU (`stt.md`) to see whether the GPU bursts are the cause, and paste both.
19. Afterwards: in Lab, the LAN world closes with the game; delete `C:\Temp\gos57` once the PR has the summary.

## For developers: the harness with fakes (no PC needed)

- `uv run gos voice benchmark --synthetic` runs the real voice client in-process on the fakes: a fake key hook and microphone, synthetic audio (a tone for speech, silence between), a wake engine that hears the tone and ends the question after 0.6 s of silence (as the real one), a fixed transcript, the fast lane on the fake model over GOS_HOME's DB (a canned answer without one), and a speaker that runs the real speech filter. `--turns N` per trigger (default 5); `--stt-ms`, `--core-ms`, `--tts-ms` add a stage's time (a slow stage fails PM14, by code). Its RAM figure is that one process. Its timings are GOS's own code on that machine, not a PC measurement.
- Without `--minutes` or `--synthetic` (and as `gos voice bench`), it reports the last `--last` turns of `logs\voice.log`, RAM not measured.
- The eval-set spoken-length check always runs when GOS_HOME has a DB: the 60 questions of `tests/evals/fast_lane_questions.tsv` through the fast lane on the fake model, each answer through the speaker's filter and lexicon, counted as heard (D248). `tests/unit/tools/test_voice_spoken_length.py` runs it on the tools' test DB and `tests/integration/test_voice_benchmark_harness.py` on a harness DB (with `GOS_REQUIRE_HARNESS_RUN=1`). It is never run on the real model (that would spend on the key).
- The live spoken-length check reads the `spoken_sentences`, `spoken_words` and `spoken_cut` fields of the measured `turn` records (the voice client logs them from WO-5.7 on; older turns have none and the check is `not measured`). In a synthetic run they are the synthetic core's answers. This is where the real model's answers are checked, on Clayton's PC.
