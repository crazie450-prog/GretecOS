# Speech-to-text: the GPU, the models and the entity-accuracy eval (WO-5.2)

Clayton runs these on the gaming PC after the WO-5.2 PR is merged and deployed, and after part A of `voice_client.md` (the talk key and the microphone). They take about 45 minutes, most of it recording 50 questions. Decisions: D182 (engines, models, the GPU, the glossary prompt and spellings), D183 (the eval set, `gos voice record` and `gos voice eval`), D184 (the smoke test on synthetic speech and what it found).

`gos` below means Clayton's release copy, `C:\GregtecOS-app\current\.venv\Scripts\gos.exe` (D91). Run everything in a normal PowerShell window. GOS itself doesn't have to be running for parts A to D; they read the DB in `%USERPROFILE%\GregtecOS\data` and write only under `%USERPROFILE%\GregtecOS` (`models\`, `data\evals\stt\`, `data\reports\`). Nothing here touches Play, Lab or Export.

What is measured here: with your own voice and headset, GOS must resolve the right item or machine in at least 48 of the 50 questions (entity accuracy >= 95%, QA:V2), with a word error rate of at most 8% as a secondary check (plan 8, WO-5.2). The container could only run a smoke test on synthetic speech with CPU models (D184), so this run is the acceptance.

## A. Setup (once)

1. Deploy: in the profile clone, `git pull`, then `uv run gos deploy`. The release now also holds the speech engines (the `stt` dependency group: faster-whisper and onnx-asr, about 400 MB; D182).
2. **Can Whisper use the GPU?**
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice stt --engine whisper-gpu
   ```
   - `GPU for Whisper: yes`: it then downloads the Whisper model `large-v3-turbo` (1.6 GB, once, into `%USERPROFILE%\GregtecOS\models\whisper`), loads it and prints `Speech-to-text: whisper large-v3-turbo on cuda (int8_float16), loaded in ... ms` and `prompt: 62 glossary forms from ...glossary.tsv`. Go to step 4.
   - `GPU for Whisper: no, cublas64_12.dll and cublasLt64_12.dll not found ...`: the NVIDIA driver is there but NVIDIA's CUDA 12 math library isn't. Do step 3.
   - `GPU for Whisper: no, no CUDA GPU ...`: the NVIDIA driver isn't working (update it from NVIDIA's site), or this PC has no NVIDIA GPU. Without one, skip to step 4: GOS uses the CPU engine.
3. **cuBLAS** (only if step 2 asked for it). NVIDIA publishes it as a Python wheel; this puts it in GOS_HOME, where GOS looks for it on Windows, and changes nothing else on the PC (no PATH edit, no installer):
   ```powershell
   uv pip install --python 3.12 --target "$env:USERPROFILE\GregtecOS\models\cuda" nvidia-cublas-cu12==12.8.4.1 nvidia-cuda-nvrtc-cu12==12.8.93
   ```
   The versions are pinned so this step gives the same files every time: CTranslate2 loads `cublas64_12.dll`, which any cuBLAS 12 provides, and 12.8.4.1 is the newest cuBLAS with a Windows wheel when this was written (NVIDIA's 12.9.3 has none). About 900 MB (cuBLAS 12 and the NVRTC library it loads; NVIDIA's own files under NVIDIA's licence, so they are never in the repo or in a release). Run step 2 again: it must now say `GPU for Whisper: yes`. If you already have NVIDIA's CUDA Toolkit 12 installed, step 2 says yes without this.
4. **The engine GOS will use** (`[voice] stt_engine = "auto"`, the default):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice stt
   ```
   With the GPU working it prints the same lines as step 2. Without it, it prints `not used: whisper large-v3-turbo on cuda (int8_float16): <why>`, downloads Parakeet (about 670 MB, once) and prints `Speech-to-text: parakeet nemo-parakeet-tdt-0.6b-v3 on cpu (int8)`. Paste the output into the PR.
5. The settings, if you want other than the defaults, in `%USERPROFILE%\GregtecOS\config\gos.toml`:
   ```toml
   [voice]
   stt_engine = "auto"            # auto | whisper-gpu | whisper-cpu | parakeet
   stt_model = "large-v3-turbo"   # or "distil-large-v3"; for whisper-cpu a small one: "small.en"
   ```
   `auto` is Whisper on the GPU when it works, else Parakeet on the CPU. The other three never fall back: if that engine can't start, every voice turn says "Speech-to-text failed." and `gos voice stt` says why.

## B. The eval set on your DB

6. The 50 questions (`tests/evals/stt_utterances.tsv`) were written over the dev runtime's DB. Check that each one names the same item in the full pack:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice eval --check
   ```
   Expect `50 of 50 lines resolve to their key on ...\gos.sqlite` and exit code 0. A line like `s44  SET   the text resolves to ...` means that question names another item in the full pack (or GOS would ask which one you meant): paste those lines into the PR and stop here; the set is fixed for the pack first, since such a line would count against speech-to-text for no fault of its own.

## C. Record the 50 questions (about 20 minutes)

7. Wear the headset as you do when playing, with GTNH running and Discord as usual, so the recordings sound like play:
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice record
   ```
   It shows one question at a time (`[1/50] s01: How do I make an LV motor?`). Hold the talk key, say it the way you would ask GOS (say abbreviations as you really say them: "L V", "E B F", "luv" or "L U V", "ass line"), release. It prints `saved s01.wav (2.4 s)` and shows the next one. A slip of the key or a whisper (`too short or too quiet`) is asked again. Ctrl+C stops; running it again goes on with the questions that have no recording yet. To redo some: `gos voice record --only s07,s23`.

   The recordings are your voice: they stay in `%USERPROFILE%\GregtecOS\data\evals\stt\` and are never committed or sent anywhere. Say each question once, naturally; don't re-record a line because the eval missed it (that would tune the test to the engine).

## D. Acceptance (plan 8 WO-5.2): entity accuracy >= 95%, word error rate <= 8%

8. ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice eval
   ```
   One line per question (`s01  ok     212 ms  wer 0/7  'How do I make an LV motor?'`; a `MISS` line also shows what GOS resolved instead), then:
   ```
   engine: whisper large-v3-turbo on cuda (int8_float16)
   entity accuracy: 49 of 50 (98.0%), target >= 95%
   word error rate: 3.1% (9 edits in 284 words), target <= 8%
   speech-to-text: P50 ... ms, P90 ... ms per segment
   verdict: met
   report: ...\data\reports\stt-eval-<time>.tsv
   ```
   **Pass:** `verdict: met` (exit code 0). Paste everything from `engine:` down, plus every `MISS` line, into the PR. The report file holds each transcript and verdict (never audio).
9. The other engines on the same recordings, so the choice of model and of CPU fallback rests on your voice and PC (each downloads its model once):
   ```powershell
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice eval --model distil-large-v3
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice eval --engine parakeet
   & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice eval --engine whisper-cpu --model small.en
   ```
   Paste each one's lines from `engine:` down. Parakeet takes no glossary prompt, so it is expected to miss more GTNH words than Whisper (D184); `P50`/`P90` show what each costs per question on your PC.
10. **If the verdict is NOT MET**, don't change anything by hand: the `MISS` lines say what was heard, and the fix is usually a glossary entry (an alias for the spelling the engine writes, or a missing term), which goes through a PR to `vault-template/glossary.tsv`. Paste the output; that is all that is needed.

## E. With the voice client

11. With GOS running and the voice token stored (`voice_client.md` step 2):
    ```powershell
    & C:\GregtecOS-app\current\.venv\Scripts\gos.exe voice run
    ```
    It prints `Loading speech-to-text...`, then `Voice client: ...; speech-to-text: whisper large-v3-turbo on cuda (int8_float16). Ctrl+C stops it.` Hold the talk key, ask "how do I make an LV motor", release: the window prints `GOS: ...` with the answer for Electric Motor (LV). (`--transcript "..."` still replaces speech-to-text with a fixed text, as in `voice_client.md` step 12.)
12. After about 20 questions, `gos voice bench` now has an `stt` stage (the segment closed to its text ready). Paste it into the PR; PM14's full latency check is WO-5.7's.

## For developers: the slow test and the smoke test (no PC needed)

- `uv sync --group stt` installs the engines in a dev clone (CI and the default `uv sync` leave them out; without them the unit tests use fake engines).
- `tests/integration/test_stt_models.py` (marked `slow`) loads a real engine and runs the eval; it is skipped unless a model is already downloaded:
  ```
  GOS_STT_TEST_HOME=<a GOS_HOME with models/ and data/gos.sqlite> GOS_STT_TEST_AUDIO=<folder of sNN.wav> \
    GOS_STT_TEST_ENGINE=whisper-cpu GOS_STT_TEST_MODEL=base.en uv run pytest tests/integration/test_stt_models.py -s
  ```
  `gos voice stt --engine whisper-cpu --model base.en --config <that GOS_HOME's gos.toml>` downloads the model there (141 MB).
- The WO-5.2 smoke test (D184) made `sNN.wav` for the 50 questions with a local text-to-speech model (Kokoro through kokoro-onnx, five voices) and ran `gos voice eval --audio <folder>` on a harness DB with CPU models. Synthetic audio, models and reports stay in scratch folders: never commit them.
