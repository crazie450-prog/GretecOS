# Gregtec OS: G5 gate review (Phase 5, voice and personas)

2026-11-12 · independent gate reviewer (D41, D158 (2)) · reviewed: `origin/main` at `84048e6` (PR #75 merged), covering the Phase 5 PRs #51 (WO-5.1), #56 (WO-5.2), #65 (WO-5.3), #61 (WO-5.4), #74 (WO-5.5), #67 (WO-5.6) and #75 (WO-5.7), plus #70 (a WO-5.3 test race) and #76 (the CI `checks` timeout that closes G2's risk 1). The first runs below were made on `64c01f1`, before #75 merged during this review; everything that #75 touches was run again on `84048e6`.

## Verdict

**G5 is ready for Clayton's in-game sign-off at the deployment test, with fakes and synthetic speech standing in for every microphone, headset, GPU and GTNH check.** All seven Phase 5 work orders are built and merged. Every acceptance criterion that can be checked without Clayton's voice, headset, GPU, GTNH and Discord is met here, on CI or both. Nothing is "not met" in code. Every §8 Phase 5 acceptance is a measurement on the PC (his voice, his GPU, his game, his Discord), so every row below is **met with fakes**, and the PC sitting below is the acceptance.

What the PC sitting must show (D158 (2) batches it with the other gates):
1. **Wake word (WO-5.4, top priority, D57):** ≥ 38/40 attempts asked and ≤ 1 false wake/h over 2 h of open-mic play. On synthetic speech the default phrase got **34/40** here, the same as PR #61 (risk 1).
2. **STT (WO-5.2):** entity accuracy ≥ 95% on his voice with `large-v3-turbo` on his GPU. CPU models on synthetic speech are below it here (risk 2).
3. **TTS (WO-5.3) and PM14 (WO-5.7):** Appendix C's default, Kokoro on the CPU, missed 400 ms again here (P50 3953 ms); Piper met it (P50 334 ms). The bench and the listening check on the PC pick the engine (risk 3).
4. **Keys in GTNH (WO-5.1), Discord for 1 h (WO-5.5), FPS/TPS/RAM (WO-5.7)** have no stand-in beyond the code audit and fakes.

Evidence tags, as in G0 to G2:
- **[repo]:** checked in the repo, the git history or GitHub's CI logs.
- **[here]:** run by this reviewer in the build container (Linux, 4 shared CPUs, no GPU, no microphone).
- **[PR]:** the implementer's PR description, not re-checked here.
- **[PC]:** needs Clayton's PC. Nothing from the PC is in the evidence yet for Phase 5.

### This reviewer's runs [here]

On `64c01f1` (#74 merged), then on `84048e6` (#75 merged):

| Check | `64c01f1` | `84048e6` |
|---|---|---|
| `uv run --locked ruff check .` | All checks passed | All checks passed |
| `uv run --locked ruff format --check .` | 326 files already formatted | 330 files already formatted |
| full `pytest` | **4977 passed, 218 skipped** (default groups, as CI) | **5019 passed, 219 skipped** (with the `stt`, `wake` and `tts` groups installed, so the engine unit tests that skip without them ran too) |
| `tests/unit/voice`, `test_personas.py`, `tools/test_persona_lanes.py` | 655 passed, 7 skipped (the Windows-only ones) | in the full run |
| `pnpm -C dashboard install --frozen-lockfile`, `build`, `test` | clean, clean, **262 passed** | #75 changes no dashboard file |
| `pnpm -C dashboard e2e` (`GOS_E2E_PORT=8971`, `GOS_E2E_FRESH_PORT=8973`, new homes) | **39 passed**, including `the persona switches from Settings' dropdown and by the spoken command (WO-5.6)` | as left |
| `xvfb-run ... ./gradlew --no-daemon --max-workers=2 runClient25 -Pharness` | `harness passed: 52 checks, run 20261112T103915Z` (2 min 33 s) | same DB |
| `GOS_REQUIRE_HARNESS_RUN=1 pytest` on `test_voice_harness`, `test_stt_harness`, `test_fast_lane_harness` | 6 passed | `test_voice_harness` passed again |
| `GOS_REQUIRE_HARNESS_RUN=1 pytest tests/integration/test_voice_benchmark_harness.py` | (not on main yet) | 2 passed |
| slow model tests with real models downloaded to a scratch GOS_HOME: `test_stt_models` (Whisper base.en, CPU), `test_tts_models` (Kokoro, Piper, Whisper small.en), `test_wake_models` (sherpa-onnx KWS, Silero, Whisper) | 2 + 3 + 4 passed; 1 skipped (`GOS_WAKE_TEST_OWW`: no openWakeWord model was trained) | not affected by #75 |

The CLI with fakes or real CPU models, in a scratch GOS_HOME whose DB was collected and built from that client harness run [here]:

| Command | Result |
|---|---|
| `gos voice doctor --config <tmp>` | the D247 report; the four Windows-only lines say `not checked: Windows only`; F13-F16 with no Discord warning; exit 0 |
| `gos persona` | three packs, `* gt-veteran`, no problems |
| `gos voice tts-bench --runs 10` (fake output, CPU, the harness build running beside it) | Piper `en_US-joe-medium`: P50 334 ms, P90 660 ms, **meets the target**; Kokoro `am_michael`: P50 3953 ms, P90 6929 ms, **misses**; GPU not measured (no GPU provider); ElevenLabs not measured (no keyring). `Latency pick: piper ... on cpu` |
| `gos voice say --phrases --tts piper --save <tmp>` | the 40 check phrases as said; every term in its `say` form (`L U V`, `E B F`, `nah-kwa-dah`, `H S S G`, ...), every number in words (`nineteen-twenty E U per tick`, `one hundred thirty-one thousand seventy-two E U per tick`), no digit, citation or id in any line |
| `gos voice wake-eval --kokoro <models> --phrase "hey archivist" --engine whisper-cpu --model base.en` | stage 1 39/40, chimed 39/40, **asked 34/40**, latency 111 ms, 10.1 min of negatives, 1 stage-1 false wake, **0 false wakes** after stage 2. Misses: 5 `no end of speech` and 1 `not detected`, all under 12 dB babble (4 min 58 s) |
| `test_stt_models.py` = `gos voice eval --audio` on 50 Kokoro-spoken lines (five voices, the lines run through the speech filter and lexicon), Whisper base.en on the CPU | entity accuracy **43 of 50 (86.0%)**, WER 4.6%, `verdict: NOT MET`. PR #56 got 46/50 with its own (uncommitted) synthesis; the two sets differ, so neither is the acceptance |
| `gos voice benchmark --synthetic --turns 5` (`84048e6`) | talk key P50 991 ms (PM14 1700: pass); wake P50 1452 ms (2200: pass); RAM free at peak 12201 MB (pass); VRAM, FPS and TPS not measured; spoken length live 10/10 and eval set 60/60 within D23, 0% cut; `Result: INCOMPLETE`, exit 0 |
| the same with typed FPS and `/forge tps` readings | 6.3% drop and 20.000 -> 20.000 TPS: `Result: PASS`, exit 0; 16.7% drop and 20.000 -> 19.221 TPS: both `FAIL`, `Result: FAIL`, exit 1. The gates fail by code |

`gos voice eval` itself could not be started from this session (its sandbox refuses any command containing the word "eval", as at G2); `test_stt_models.py` and `test_stt_harness.py::test_gos_voice_eval_check_on_the_harness_db` run the same code. No audio, model, report or DB from these runs is committed; they stay in the session's scratch folder.

### CI on `main` [repo]

| Commit | Run | ubuntu | windows | mod | dashboard |
|---|---|---|---|---|---|
| `8b1bb87` (#51, WO-5.1) | 42953544836 | success | success | success | (no job yet) |
| `00e2185` (#56, WO-5.2) | 43019835592 | success | success | success | success |
| `4053b4b` (#61, WO-5.4) | 43028695374 | success | success | success | success |
| `610780e` (#65, WO-5.3) | 43074629837 | success | success | success | success |
| `efaef2f` (#70, the WO-5.3 test race) | 43084265062 | success | success | success | success |
| `b7b88a0` (#67, WO-5.6) | 43084303461 | success | **cancelled** (G2's risk 1: an intermittent WO-5.2 test, then a 6 h hang) | success | success |
| `64c01f1` (#74, WO-5.5) | 43199691053 | success | success | success | success |
| `84048e6` (#75, WO-5.7, head) | 43201386149 | success (5025 passed, 213 skipped) | success (5047 passed, 191 skipped) | success | success |

The `mod (Windows)` and `dashboard (Windows)` workflows were green on every merge where they ran (they have path filters). PR #76 (`e028876`) gave the `checks` job a timeout, so the 6 h hang of risk 1 at G2 can't recur; the race itself was fixed by #70.

The `mod` job on `84048e6` (job 131125414753) ran the server harness (31 checks), two client harness runs (52 each) and `GOS_REQUIRE_HARNESS_RUN=1 pytest tests/integration`: **128 passed, 20 skipped**, including `test_stt_harness` (4), `test_voice_benchmark_harness` (2) and `test_voice_harness` (1).

---

## Phase 5 work orders

### WO-5.1: Voice client

> **Acceptance:** "The key and hotkeys work with GTNH focused, fullscreen and borderless, and trigger nothing in GTNH or Discord"

**Verdict: met with fakes; the acceptance itself is a PC step.** PR #51, merge `8b1bb87`; D179-D181.

| Part | Status | Evidence |
|---|---|---|
| WASAPI 16 kHz mono, 300 ms pre-roll | met with fakes | `audio.py`; `tests/unit/voice/test_audio.py` with `FakeSource`/`WavSource` [here]; real capture is `voice_client.md` steps 8-9 |
| Talk key, hold **and** toggle, on a key unbound in GTNH and Discord [QA:V8] | met; keys pending Clayton | `test_ptt.py::test_hold_mode`, `test_toggle_mode`; only F13-F24, Pause and Scroll Lock are allowed (`test_keys.py::test_only_keys_that_type_nothing_and_are_unbound_by_default_are_allowed`); `gos voice keys play\|lab` reads `options.txt` read-only [here] |
| pynput hook thread that only enqueues; watchdog re-installs it | met | `test_the_filter_queues_only_the_watched_keys`, `test_a_press_the_hook_missed_is_passed_on_and_the_hook_reinstalled`, and on Windows CI the real hook: `test_the_real_hook_sees_an_injected_key`, `test_the_watchdog_catches_a_silently_removed_hook` [repo] |
| Hotkeys confirm, cancel, next card | met | `test_ptt.py::test_hotkeys_act_on_the_press_only`; `tools/test_fast_lane.py::test_a_confirm_or_cancel_with_nothing_waiting_changes_nothing`, `voice/test_core_routes.py::test_a_confirm_or_cancel_with_nothing_waiting_isnt_pushed` [here] |
| Barge-in | met | by key (WO-5.1) and by voice (WO-5.4's `test_barge_in_by_voice`) [here] |
| Wake word as main trigger | met with fakes | WO-5.4 fills the hook |
| End to end on a real DB | met with fakes | `test_voice_harness.py::test_a_spoken_question_is_answered_from_the_harness_db` [here, repo] |
| Keys work in GTNH borderless and exclusive fullscreen; nothing fires in GTNH or Discord | **PC step** | `voice_client.md` B7-B11 |

Open: PR #51 Q1-Q7 and Q10 (below). Q8 is done by WO-5.4; Q9 by WO-5.2's change to `gos deploy` (`deploy.GROUPS`).

### WO-5.2: Speech-to-text

> **Acceptance:** "**Entity accuracy** (item or machine resolved correctly) ≥ 95% on a 50-utterance GTNH set [QA:V2]; word error rate ≤ 8% as a secondary check"

**Verdict: met with fakes; the acceptance itself is a PC step.** PR #56, merge `00e2185`; D182-D184.

| Part | Status | Evidence |
|---|---|---|
| faster-whisper `large-v3-turbo`/`distil-large-v3`, int8_float16, beam 1, `en`, glossary hotwords; Parakeet v3 CPU fallback | met | `stt.py`; `tests/unit/voice/test_stt.py`; the real engines load and transcribe in `test_stt_models.py` [here] |
| The 50-utterance set, recorder and eval | met | `tests/evals/stt_utterances.tsv`; `gos voice record`, `gos voice eval`; on the harness DB every line resolves to its key and scores 100% (`test_stt_harness.py`, 4 passed) [here, repo] |
| Entity accuracy ≥ 95%, WER ≤ 8% | **PC step** | Synthetic speech here: Whisper base.en on the CPU 43/50 (86%), WER 4.6%. PR #56: base.en 46/50, small.en 46/50, large-v3-turbo 47/50 (CPU), Parakeet 37/50 [PR]. `stt.md` D8-D9 is the acceptance |

The misses here were synthetic-speech confusions ("LUV machine haul", "silicon bull", "APOC-ID" for Epoxid, "Asline"). Most of them are what a glossary alias fixes (`stt.md` step 10), but they come from a TTS voice, not Clayton's, so no alias is proposed from them.

Open: PR #56 Q1, Q2, Q7, Q9, Q11 (Q3-Q6, Q8 and Q10 were agreed by its reviewer and need only Clayton's "keep").

### WO-5.3: Text-to-speech

> **Acceptance:** "First audio ≤ 400 ms after the first sentence; 30 GTNH terms and 10 number/unit phrases spoken correctly; no citation or ID is ever spoken"

**Verdict: met with fakes; latency on his PC and the listening check are PC steps.** PR #65, merge `610780e`, and #70; D203-D205.

| Part | Status | Evidence |
|---|---|---|
| Kokoro, Piper (own ONNX runner, not the GPL `piper-tts`), optional ElevenLabs; fallback chain | met | `tts.py`; `test_tts.py`; `test_tts_models.py::test_each_engine_speaks_through_the_speaker[kokoro\|piper]` [here] |
| First audio ≤ 400 ms | met by Piper on this CPU; **PC step** | P50 334 ms here, 246 ms in PR #65; Kokoro on the CPU misses (3953 ms here, 3392 ms in PR #65) [here, PR]. `tts.md` B3-B5 |
| 30 terms and 10 number/unit phrases | met by text; **PC step** by ear | `test_speech_filter.py::test_each_check_phrase_is_said_as_written` (all 40 rows), `gos voice say --phrases` output above, `test_the_round_trip_hears_most_check_phrases` (Kokoro to Whisper) [here]. `tts.md` D7 |
| No citation or ID is ever spoken | met | `test_citations_and_ids_go`, `test_property_no_citation_or_id_is_ever_spoken` (2,000 answers), `test_the_filter_removes_whatever_the_fast_lane_calls_a_ref_or_key` [here] |
| Output to the Arctis Chat endpoint (D56) | met with fakes; **PC step** | `test_speech_plays_shared_on_the_chat_endpoint_not_the_default` [here]; `tts.md` A2 |
| Windows communications ducking "Do nothing" | met on Windows CI; **PC step** | `scripts/set_comm_ducking.ps1` and its Windows test on a throwaway HKCU key [repo]; `tts.md` E9 |
| Measure Kokoro CPU and GPU, Piper, ElevenLabs; log VRAM | partly | CPU measured here; GPU needs `onnxruntime-gpu` in a throwaway clone (`tts.md` B4), and a GPU pick can't ship in the release until a follow-up change (D203) |

Open: PR #65 Q1-Q6.

### WO-5.4: Wake word

> **Acceptance:** "≤ 1 false wake/h over 2 h of open-mic Discord play; ≥ 38 of 40 wake attempts succeed; wake-path latency per PM14"

**Verdict: met with fakes; all three numbers are PC steps.** PR #61, merge `4053b4b`; D185-D187.

| Part | Status | Evidence |
|---|---|---|
| sherpa-onnx KWS primary; openWakeWord on ONNX (no tflite); Whisper second stage; chime + 1.5 s cancel window; barge-in by voice (D180) | met | `wake.py`; `test_wake.py` (`test_a_look_alike_is_dropped_silently_by_the_second_stage`, `test_no_question_in_the_cancel_window_drops_the_wake`, `test_a_blip_between_the_phrase_and_the_question_doesnt_start_it`, `test_barge_in_by_voice`); `test_wake_models.py`, 4 real-model tests [here] |
| Candidate phrases tested (PM08) | met on synthetic speech | PR #61's table over eight phrases [PR]; `hey archivist` re-run here: 34/40 asked, 0 false wakes in 10.1 min |
| ≥ 38/40 attempts succeed | **PC step** (synthetic: 34/40) | `wake.md` B3 and D6. Risk 1 |
| ≤ 1 false wake/h over 2 h | **PC step** | `wake.md` C5 and D6 |
| Wake-path latency per PM14 | met with fakes; **PC step** | `gos voice benchmark --synthetic` wake P50 1452 ms here; `voice_benchmarks.md` C |

Open: PR #61 Q1-Q6.

### WO-5.5: Discord coexistence

> **Acceptance:** "Discord voice is unaffected during a 1 h session with GOS active"

**Verdict: met with fakes (the code audit); the 1 h session is a PC step.** PR #74, merge `64c01f1`; D246-D247.

- Shared-mode WASAPI everywhere, the chime moved to the Chat endpoint, no default-device writes, a pass-through hook, and a static audit with DLL and import allowlists: `test_discord_coexistence.py` (`test_every_wasapi_stream_is_opened_shared`, `test_the_wake_chime_plays_on_the_configured_endpoint`, `test_the_hook_passes_every_key_on`, `test_only_known_windows_functions_are_called`, `test_the_package_imports_only_its_own_modules_by_name`, ...) [here]. On Windows CI the real registry read and the real hook install and removal ran [repo].
- `gos voice doctor` ran here (Linux lines only) [here].
- The 1 h session: **PC step**, `discord_coexistence.md` A-C, with the pass/fail table of D.

Open: PR #74's six items.

### WO-5.6: Persona packs

> **Acceptance:** "Switch by voice in ≤ 1 s; the next deep-lane reply uses the new persona; a 4th pack made from `_template.md` loads with no code change"

**Verdict: met with fakes; the real-microphone switch and the board are PC steps.** PR #67, merge `b7b88a0`; D206-D208.

| Part | Status | Evidence |
|---|---|---|
| 3 packs + template, default `gt-veteran` (D60) | met | `test_the_shipped_packs_load_clean`, `test_the_default_is_gt_veteran`; `gos persona` here |
| Switch by voice in ≤ 1 s | met with fakes; **PC step** | `test_persona_voice.py::test_switch_by_voice_within_a_second` (fake STT transcripts through the real client, router and packs, with a fast lane that fails if called) [here] |
| The next deep-lane reply uses the new persona | met with fakes | `test_persona_lanes.py::test_the_next_deep_lane_reply_uses_the_new_persona` [here] |
| 4th pack from `_template.md`, no code change | met | `test_personas.py::test_a_fourth_pack_from_the_template_loads_with_no_code_change` [here] |
| Voice per persona | met with fakes; **PC step** by ear | `test_kokoro_and_elevenlabs_change_voice_without_reloading` [here] |
| Never-rules can't be overridden; agents only draft | met | `test_a_pack_cannot_override_the_never_rules`, `test_agent_drafts_never_go_live` [here] |
| Settings persona dropdown | met in e2e; **needs Clayton's board approval** (D92, D158 (5)) | e2e `the persona switches from Settings' dropdown and by the spoken command (WO-5.6)` [here] |

Open: PR #67 Q1-Q5.

### WO-5.7: Benchmarks with GTNH running

> **Acceptance:** "Latency per PM14; FPS drop ≤ 10%; no TPS impact; ≥ 4 GB RAM free at peak; spoken answers ≤ 2 sentences / 40 words in 95% of eval items (D23)"

**Verdict: met with fakes; every number is a PC step.** PR #75, merged during this review as `84048e6` (CI run 43201386149 green on all four jobs); D248-D249.

| Part | Status | Evidence |
|---|---|---|
| Latency per PM14, both triggers | met with fakes; **PC step** | `test_pm14_is_checked_per_trigger_on_the_p50`, `test_a_slow_synthetic_turn_fails_pm14_by_code`; synthetic run here: 991 ms and 1452 ms [here] |
| FPS drop ≤ 10%; no TPS impact (≤ 0.1, D249) | met with fakes; **PC step** | `test_fps_drop_and_tps_thresholds`, `test_a_stale_tps_reading_is_not_measured`; PASS and FAIL runs here with typed readings [here]. `voice_benchmarks.md` B-C, on Lab only |
| ≥ 4 GB RAM free at peak [QA:A7]; VRAM | met with fakes; **PC step** | `test_ram_free_at_peak_is_the_gate`, `test_the_sampler_reads_ram_and_vram`; 12201 MB here (container) |
| Spoken length within D23 in ≥ 95% | met on the eval set with the fake model; **PC step** for the real model's live answers | `test_voice_benchmark_harness.py::test_the_eval_set_is_spoken_within_d23` (60/60, 0 cut) [here, repo]; the live gate reads the real model's turns on the PC |

The plan says "95% of eval items". D248 reads it as the eval set on the fake model and adds the same gate on the live turns, where the real model's answers are checked without paying for an eval run. That reading is reasonable: the fake model's answers say nothing about the real model's length, and the live gate does.

Open: PR #75 Q1-Q4 (Q5, "merge only once Windows and mod are green", was met: run 43195456319 on its head was green).

---

## G5 row

> **G5:** "Reviewer report; Clayton sign-off (in game)"

| Criterion | Verdict | Evidence and gaps |
|---|---|---|
| Reviewer report | met | this report |
| The Phase 5 acceptances | **met with fakes** | all seven WOs above; nothing is not met in code |
| Voice works end to end | met with fakes | `test_voice_harness` (a talk-key question through the real client and core to the harness DB), `gos voice benchmark --synthetic` (both triggers through the real client) [here, repo] |
| Clayton's sign-off, in game | **PC step** | the steps below, then his sign-off |
| §10's day-one voice line (wake ≤ 1/h with ≥ 38/40, talk key backup, entity accuracy ≥ 95%, PM14, pronunciation and filter, personas) | PC step | the same measurements; G11 re-checks them |

**Required before Clayton signs G5:**
1. The PC steps below, with their paste-backs.
2. His answers, or "keep as built", to the Phase 5 questions below (D158: the defaults stand and are reversible).
3. A decision on the engine defaults after the runs: `tts` (Kokoro or Piper), `stt_engine` fallback (Parakeet or `whisper-cpu small.en`), and the wake phrase.

Keep `main` green on all four jobs up to the commit he deploys. It is green on `84048e6` (run 43201386149).

Should do (not blocking):
- Doc drift in the runbooks: `voice_client.md` steps 12 and 15 and `wake.md` step 9 still say "until WO-5.2" or "spoken once WO-5.3 lands"; both have landed. Harmless, for the PM.
- Plan §8 has no row for the follow-ups that Phase 5's PRs leave unslotted: the `speak` tool and `timer_set` (PR #65 Q3, D205), `persona_set` (PR #67 Q3, D208), the GPU TTS release change (D203) and an STT engine timeout (PR #51 Q10). The PM should slot them.

---

## VERIFY items Phase 5 touched

| ID | WO | Status now | What closes it |
|---|---|---|---|
| **V11** (headset device names) | 5.3 | Closed by D56 (Arctis Nova Elite, TTS on the Chat endpoint) | Nothing re-opens it. The device-name part is confirmed by `gos voice devices` showing the Chat endpoint `[used]` (PC step 1 below; PR #65) |
| **V12** (Discord Push To Mute) | 5.5 | Dropped by D58 | Nothing. PR #74 Q2 asks Clayton to confirm that binding the talk key to Discord's Push To Mute stays not recommended |

No other §13.2 row names a Phase 5 WO, and no Phase 5 PR added or changed a row in `verify.tsv`. QA:V8 (talk key) and PM08 (wake phrase) are not V rows; the steps below check both.

---

## Open questions from Phase 5's PRs

None is answered by a later decision (D209-D249 hold no answers to them). The defaults stand, reversible, until Clayton answers (D158). "Agreed" means the PR's reviewer agreed; Clayton still decides.

**PR #51** (WO-5.1), https://github.com/crazie450-prog/GretecOS/pull/51
1. Q1: keys talk F13 (hold), confirm F14, cancel F15, next card F16; only F13-F24, Pause and Scroll Lock allowed (D180). Keep, or name others?
2. Q2: hold by default, toggle available. OK?
3. Q3: no mouse buttons directly; map one to F13-F24 in the mouse software. OK?
4. Q4: nothing is suppressed, so the keys must stay unbound in GTNH and Discord. OK?
5. Q5: the cancel hotkey's behaviour (D179). OK?
6. Q6: "Core offline." once per outage; a refused token reported as such. OK?
7. Q7: should deep-lane results asked from Discord or the dashboard also be spoken?
8. Q10: should speech-to-text get a timeout, so a hung engine can't hold speech? (Not done by WO-5.2.)

**PR #56** (WO-5.2), https://github.com/crazie450-prog/GretecOS/pull/56
9. Q1: should `auto` fall back to `whisper-cpu small.en` instead of Parakeet? Decide after `stt.md` step 9.
10. Q2: cuBLAS 12.8.4.1 as a pinned PC step, not a locked dependency. OK?
11. Q3-Q6 and Q8 (hotwords only; segment mode; `gos voice eval`; strict scoring; "what use is X" for typed questions too): agreed; keep?
12. Q7: the snapper's word rule (only lower-case "a" and "i" barred from a spelled run). OK?
13. Q9: GOS_APP grows by about 400 MB per release because of the `stt` group. OK?
14. Q10-Q11: the synthetic-speech script stays uncommitted; a merge commit's `# Conflicts:` note isn't rewritten. Noted.

**PR #65** (WO-5.3), https://github.com/crazie450-prog/GretecOS/pull/65
15. Q1: phonemizer and espeak-ng (GPL-3.0) installed from PyPI, never vendored. Accept, or a follow-up with an Apache-2.0 G2P?
16. Q2: the default engine (Kokoro on the CPU until his run decides).
17. Q3: which WO takes the `speak` tool and `timer_set`?
18. Q4: ElevenLabs voice (Adam until he picks one).
19. Q5: "12:30" read as a time, a spaced "A" before a word as a letter, "m" as minutes only at a clause's end. Confirm?
20. Q6: an engine is dropped for the session after 3 failures in a row (not a cool-down). OK?

**PR #61** (WO-5.4), https://github.com/crazie450-prog/GretecOS/pull/61
21. Q1: the phrase: `hey archivist` (D186) or `hello quartermaster` if he calls GOS "the archivist" with friends?
22. Q2: a stage-1 look-alike stops GOS speaking even if stage 2 rejects it. OK?
23. Q3: GOS stops when he talks for 0.3 s while it speaks. Keep?
24. Q4: openWakeWord's pipeline re-implemented (Apache-2.0), not the package. OK?
25. Q5: the 1.5 s cancel window; longer after trying it live?
26. Q6: the 0.1 s onset confirmation.

**PR #74** (WO-5.5), https://github.com/crazie450-prog/GretecOS/pull/74
27. D246: is there a key he binds in Discord that GOS should warn about (today only Pause and Scroll Lock)?
28. D246: binding the talk key to Discord's Push To Mute stays not recommended. Confirm?
29. D247: any ducking value other than 3 is a `!` line with exit 1 (agreed).
30. D247: the wake chime moved to the Chat endpoint (agreed).
31. D247: `gos voice doctor` as its own subcommand; shared mode as a named constant (agreed).
32. L5 and L6 accepted as is (the Linux chime's fake output; the fallback warning logged twice).

**PR #67** (WO-5.6), https://github.com/crazie450-prog/GretecOS/pull/67
33. Q1: shipped packs in `_gos/personas/`, his in `personas/`, agents only in `personas/drafts/`. OK? A "promote draft" button later?
34. Q2: the voices per pack (am_fenrir/Arnold, bf_emma/Rachel, am_puck/Josh). Keep, or pick after listening?
35. Q3: which WO takes `persona_set` and the `persona` subagent?
36. Q4: a name of more than 3 words, or one with words like "for", "of" or "recipe", isn't a switch. OK for his own pack names?
37. Q5: approve the Settings persona panel (D92).

**PR #75** (WO-5.7), https://github.com/crazie450-prog/GretecOS/pull/75
38. Q1: the "cut by the filter" share gated at ≤ 5%, or info only?
39. Q2: words counted as heard (each written word once; numbers as said; at most 5 letters per spelled run). The reading of D23 he wants?
40. Q3: "no TPS impact" = mean drop ≤ 0.1 TPS, with the stale-reading guard. OK?
41. Q4: D179's Play pre-launch hook moved to WO-6.1 (D249). Agree? Until WO-6.1, he starts `gos voice run` by hand.

PR #70 and PR #76 had no questions.

---

## Clayton's PC steps before signing G5

These run after G2's PC steps 0 to 3 (`docs/reviews/G2_review.md`: Node.js and pnpm, `phase1_pc_checks.md` steps 1 to 12, the setup wizard with the headset's Chat endpoint, the talk-key row and the API key) and don't repeat them. They follow the Phase 5 runbooks in an order that runs each shared step once. `$gos` is `C:\GregtecOS-app\current\.venv\Scripts\gos.exe`, as in `phase1_pc_checks.md`. Use a normal PowerShell window in his own account (never "Run as administrator"), with `$ProgressPreference = 'SilentlyContinue'`. Every game step is in **GTNH-Lab**; nothing here writes to Play. The two long steps (7 and 10) are ordinary play and can be on different days. In all, about 6.5 h, of which about 3.5 h is play.

Spending: every voice question is one fast-lane call on the key (G2 step 5's 60-question eval was about 1 US cent; the steps below ask about 70 questions of the model: the STT eval and persona switches make no model call). ElevenLabs (step 4) is optional and paid.

**0. Deploy a green `main`:** `84048e6` or later, with all four CI jobs green on that commit (`84048e6`: run 43201386149). In the profile clone: `git pull`, then `uv run gos deploy`. The release then holds the `stt`, `wake` and `tts` groups (D182, D185, D203). GOS running: `& $gos status` shows the core `ok`.

**1. Voice setup** (`voice_client.md` A2-A6, `tts.md` A2):
```powershell
& $gos token new voice
& $gos voice devices
```
- Map a mouse or macro key to F13 (and F14-F16) in SteelSeries GG, or set other keys under `[voice]` in `gos.toml` (`voice_client.md` A3).
- In `gos voice devices`, the Arctis microphone must be `used` (else `[voice] input_device = "Arctis"`, and always when another microphone is listed), and under `Output (output_endpoint = 'Chat')` the Arctis Chat endpoint must be `[used]` (else its exact name in `output_endpoint`). Paste the output (V11).
- With GTNH closed: `& $gos voice keys play` and `& $gos voice keys lab`. Expect `free in GTNH` for all four keys, exit 0.
- Discord, User Settings > Keybinds: none of the four keys is bound. (G2 step 3's wizard row checked Play's binding of the talk key only; this checks all four, in both instances.)

**2. Ducking** (`tts.md` E9), his own setting:
```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File C:\GregtecOS-app\current\scripts\set_comm_ducking.ps1
```
Restart Discord and GTNH. Control Panel > Sound > Communications shows "Do nothing".

**3. Speech-to-text** (`stt.md` A2-A4, B6, C7, D8-D9), about 45 min:
```powershell
& $gos voice stt --engine whisper-gpu
```
If it says cuBLAS isn't found, run `stt.md` A3's pinned `uv pip install ... nvidia-cublas-cu12==12.8.4.1 nvidia-cuda-nvrtc-cu12==12.8.93`, then the line again until `GPU for Whisper: yes`. Then:
```powershell
& $gos voice stt
& $gos voice eval --check
& $gos voice record
& $gos voice eval
& $gos voice eval --model distil-large-v3
& $gos voice eval --engine parakeet
& $gos voice eval --engine whisper-cpu --model small.en
```
`--check` must say `50 of 50 lines resolve`; if not, paste the `SET` lines and stop this step. Record with the headset on, GTNH (Lab) running and Discord as usual. **Pass:** `gos voice eval` ends `verdict: met`. Paste everything from `engine:` down for each run, and every `MISS` line. The other three runs decide PR #56 Q1.

**4. Text-to-speech** (`tts.md` B3-B5, C6, D7-D8), about 30 min, with Lab running:
```powershell
& $gos voice tts-bench
& $gos voice say --phrases
& $gos voice say --phrases --tts piper
& $gos voice tts-check
```
- **Pass:** the engine he keeps has P50 ≤ 400 ms in the bench, and all 40 phrases sound right by ear (note the ids of any that don't). Then set `tts` and `tts_device` in `gos.toml` (`tts.md` B5). Here Kokoro on the CPU missed by about ten times.
- Optional, GPU: `tts.md` B4 in a throwaway clone (note `VRAM +... MB`). Optional and paid, ElevenLabs: `tts.md` C6.

**5. Keys in GTNH** (`voice_client.md` B7-B11), the WO-5.1 acceptance. Lab running, a world loaded, Discord voice with open mic:
```powershell
& $gos voice test --seconds 600
```
Borderless (F11), then exclusive fullscreen (`window.borderless=false` in Lab's `config\lwjgl3ify.cfg`, set back to `true` after). **Pass:** every press and release listed with `(hook N ms)`, none missed; the 2 s press gives an about 2.4 s segment that isn't `silent`; nothing happened in GTNH or Discord; the summary shows `blocks dropped: 0`.

**6. The client with the core** (`voice_client.md` C12-C14, `stt.md` E11):
```powershell
& $gos voice run
```
Ask "how do I make an LV motor" with the talk key: it is spoken on the Chat endpoint, naming Electric Motor (LV) first. Then `gos stop` / `gos serve` in another window (`GOS: Core offline.` once, then answered again), and unplug the headset for 5 s (`Microphone lost.` once, then answered again). Ctrl+C.

**7. Wake word** (`wake.md` A2, B3, C5, D6-D8, E9-E10), the WO-5.4 acceptance, about 2.5 h:
- Set `[voice] wake_phrase` first (PR #61 Q1; default `hey archivist`).
- 40 attempts, then 2 h of open-mic play in a session where he does **not** use GOS on purpose (so step 10's session can't be this one: its deliberate wakes would count as false wakes):
  ```powershell
  & $gos voice test --save "$env:USERPROFILE\GregtecOS\data\evals\wake\wake"
  & $gos voice capture "$env:USERPROFILE\GregtecOS\data\evals\wake\play\session-1.wav" --minutes 120
  & $gos voice wake-eval --audio "$env:USERPROFILE\GregtecOS\data\evals\wake"
  & $gos voice wake-eval --audio "$env:USERPROFILE\GregtecOS\data\evals\wake" --no-verify
  ```
- **Pass:** exit 0, `asked` ≥ 38/40 and `per h` ≤ 1.0 over ≥ 120 min. If not, `wake.md` D7's threshold and phrase tries, one at a time. Paste each table and every `missed` and `false wake` line.
- Live: `& $gos voice run`; "Hey archivist, how do I make steel": a chime, then the spoken answer; talk for a third of a second while GOS speaks: it stops (barge-in by voice).
- The recordings are his voice and his friends' names: keep them in GOS_HOME and delete them after the paste.

**8. Personas** (PR #67's steps), about 10 min, with the core and `gos voice run`:
- `& $gos persona`: three packs, `* gt-veteran`, no problems.
- Say "persona ship AI": "Ship AI here. ..." starts within about a second, in the `bf_emma` voice. Then "persona coop buddy" and "persona GT veteran".
- Listen to each voice (to change one: copy the pack to `vault\personas\` and edit its `voice:` line).
- Only with ElevenLabs: check that the three shipped voice ids exist on his account.
- In Settings, switch with the dropdown, and give his board verdict (D92).

**9. Discord, before** (`discord_coexistence.md` A2-A6; its A1 is step 0 and its A4 is step 1):
```powershell
New-Item -ItemType Directory -Force C:\Temp\gos55 | Out-Null
& $gos voice doctor | Tee-Object C:\Temp\gos55\doctor_before.txt
```
Expect exit 0 and no `!` line. Note Discord's Voice & Video settings and Windows' default and Default Communication Devices (`mmsys.cpl`), as A5-A6 say.

**10. The 1 h Discord session** (`discord_coexistence.md` B7-B12 and C13-C15), the WO-5.5 acceptance. Lab, open mic, at least one friend told beforehand, `& $gos voice run` for the hour, about 12 GOS turns mixing the wake word, the talk key, barge-in, the hotkeys and questions while a friend talks. Afterwards:
```powershell
& $gos voice doctor | Tee-Object C:\Temp\gos55\doctor_after.txt
Compare-Object (Get-Content C:\Temp\gos55\doctor_before.txt) (Get-Content C:\Temp\gos55\doctor_after.txt)
& $gos voice bench | Tee-Object C:\Temp\gos55\bench.txt
```
**Pass:** no `Compare-Object` output, Discord's and Windows' settings unchanged, and every row of `discord_coexistence.md` D's table a pass. `bench.txt` also has the `wake word: N detections, M chimed` line that `wake.md` E11 reads (at most 2 unmeant chimes in 2 h of live use).

**11. Benchmarks with GTNH running** (`voice_benchmarks.md` A2-A4, B5-B9, C10-C15, D16-D18), the WO-5.7 and PM14 acceptance, about 45 min, **Lab only** (Open to LAN with cheats, for `/forge tps`):
```powershell
New-Item -ItemType Directory -Force C:\Temp\gos57 | Out-Null
$lab = 'C:\Games\PrismLauncher\instances\GTNH-Lab\.minecraft'
```
- Baseline with GOS stopped (`& $gos stop`): F3 five times into `fps-before.txt`; `/forge tps` twice; `Copy-Item "$lab\logs\latest.log" C:\Temp\gos57\tps-before.log`.
- Then `& $gos serve` and `& $gos voice run`, and in another window:
  ```powershell
  & $gos voice benchmark --minutes 20 `
      --fps-before C:\Temp\gos57\fps-before.txt --fps-during C:\Temp\gos57\fps-during.txt `
      --tps-before C:\Temp\gos57\tps-before.log --tps-during C:\Temp\gos57\tps-during.log `
      --out C:\Temp\gos57\bench.tsv
  ```
- 20 talk-key and 20 wake-word questions; F3 at about minute 10 into `fps-during.txt`; `/forge tps` twice and the log copy to `tps-during.log` at about minute 15.
- **Pass:** `Result: PASS` (exit 0). `INCOMPLETE` is not a pass. Delete `C:\Temp\gos55` and `C:\Temp\gos57` after pasting.

**12. Sign-off, in game.** With the engines, phrase and keys he chose in steps 3, 4 and 7 set in `gos.toml`, an ordinary Lab session using GOS by voice is the G5 sign-off. Say whether to keep each default listed under the questions above.

**What to paste back for G5:**
- step 1's `gos voice devices` and `gos voice keys` output;
- step 3's four `gos voice eval` summaries and `MISS` lines;
- step 4's `tts-bench` output, the ids of any phrase that sounded wrong, and the chosen engine;
- step 5's `gos voice test` summary for both window modes (and any watchdog re-install, with what he was doing);
- step 7's `wake-eval` tables and `missed`/`false wake` lines, and the chosen phrase and threshold;
- step 8's persona observations and board verdict;
- steps 9-10: `doctor_before.txt`, `doctor_after.txt`, the `Compare-Object` output, Discord and Windows settings before and after, the filled-in table, `bench.txt`;
- step 11's `bench.txt` (and the TSV if a check failed);
- his answers to the questions above.

---

## Risks

1. **The wake word may miss its 38/40 target.** On synthetic speech `hey archivist` reached 34/40 here and in PR #61. Five of the six misses were `no end of speech` under 12 dB babble: the question never ends while background speech goes on. On the PC, friends' voices come through the headset, not the microphone, so the babble should be lower, but a loud game or a room with other voices would hit the same cause. The levers are in `wake.md` D7 (threshold, phrase) and the 0.6 s end-of-speech rule (D185). WO-5.4 is the top-priority gate (D57). (Likely)
2. **STT on the CPU falls short of 95%.** CPU models on synthetic speech gave 74-94% (PR #56) and 86% here. `auto` falls back to Parakeet, the weakest (74%). If his GPU path fails (driver, cuBLAS), voice accuracy drops sharply. `stt.md` A2-A3 and PR #56 Q1 cover it. (Confirmed on synthetic speech)
3. **Appendix C's TTS default misses its targets on a CPU.** Kokoro on the CPU was 3.4-4.0 s to first audio, here and in PR #65, which also breaks PM14's 1.7 s. Piper meets 400 ms but was the less intelligible voice in PR #65's round trip (32/40 against Kokoro's 39/40). Running Kokoro on the GPU needs a release change no WO owns yet (D203). (Confirmed in the container; the PC decides)
4. **GPL runtime dependencies** (phonemizer, espeak-ng) are installed, not vendored (PR #65 Q1). This is consistent with CLAUDE.md's rule (no GPL code vendored into core), but it is his call. (Confirmed)
5. **Real-model spoken length is unchecked until the PC run.** The eval set runs on the fake model; only the live gate (WO-5.7) sees the real model's answers, and a verbose model would fail it, with the fix in the fast lane's prompt rules (PR #75 Q1). (Likely)
6. **Voice doesn't start with Play** until WO-6.1 takes D179's pre-launch hook (D249). Until then he starts `gos voice run` by hand each session. (Confirmed)
