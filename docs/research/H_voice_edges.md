# H — Voice edge cases: wake word, Discord open mic, screen capture, GTNH wiki

Research date 2026-11-01. This file builds on D_runtime_voice_memory.md and does not repeat it (Porcupine's free tier ended, pynput for hold-to-talk, RealtimeSTT needs Python <3.13).

Tags: **CONFIRMED** (source) means I read it in a primary source or verified it by inspecting the package. **UNCERTAIN** (reason) means it is not verified. **[design]** marks my own engineering inference.

---

## 0. Bottom line

1. **Wake word.** Use sherpa-onnx keyword spotting to prototype: it needs no training, is maintained, and has cp312/cp313 Windows wheels. If it false-triggers too often, train an openWakeWord-format model with **livekit-wakeword** (in WSL2 or a cloud GPU) and run it on Windows with onnxruntime. openWakeWord 0.6.0 still installs on 3.12/3.13 Windows but must be created with `inference_framework="onnx"`. **Do not use "GregTech/Gregtec" in the wake phrase**, because GTNH players say that word constantly. [design]
2. **Discord.** Bind the assistant's hold-to-talk key to Discord's **"Push To Mute"** keybind as well, so Discord mutes for exactly as long as the key is held and nothing has to be simulated. For the wake-word path, mute through Discord RPC `SET_VOICE_SETTINGS {mute:true}` using a personal Discord app. An alternative is a Voicemeeter gate on the mic feed that Discord uses. The wake phrase itself will always reach your friends.
3. **Capture.** **dxcam 0.3.0** (revived 2026; DXGI plus WGC backends) for one-shot grabs. Turn on lwjgl3ify's `borderless` option so the game stays DWM-composited. mss is the simple fallback. Crop to the Minecraft window.
4. **GTNH wiki.** It is a MediaWiki site, but from this sandbox both the API and the licence could not be checked: the host is blocked by the session proxy, and WebFetch got a site-side `client_error`. Check both from your own PC with one `siteinfo` call. Follow MediaWiki etiquette: serial requests, a descriptive User-Agent, batched titles, `maxlag`, and a revision-keyed local cache.

---

## 1. Wake word on Windows without Picovoice

### 1.1 openWakeWord — status on Python 3.12/3.13
- PyPI's latest is still **0.6.0, uploaded 2024-02-11**, as a single pure-Python wheel. Its dependencies are `onnxruntime>=1.10,<2`, tqdm, scipy, scikit-learn and requests, plus `tflite-runtime` **only on Linux** (`platform_system == "Linux"`). CONFIRMED (https://pypi.org/pypi/openwakeword/json)
- The GitHub `main` branch is **not dead**, but none of its changes have been released to PyPI. 2025-10-17: "Bump python version to 3.10+, switch tflite to new ai-edge-litert". 2025-12-30: "put onnx to tflite conversion behind flag in training code". 2025-09-02: "Remove outdated colab link". `setup.py` on main has `python_requires=">=3.10"`, and its version is still `0.6.0`. CONFIRMED (shallow clone of https://github.com/dscripka/openWakeWord, git log read 2026-11-01)
- **Installability.** A `pip install --dry-run --platform win_amd64 --only-binary=:all:` run resolved all of 0.6.0's Windows runtime dependencies to binary wheels for **both cp312 and cp313** (onnxruntime 1.31.1, scipy 1.18.1, scikit-learn 1.9.1, numpy 2.5.4). The wheel's runtime modules contain no `pkg_resources`/`distutils` imports and no removed numpy aliases. CONFIRMED (my resolver run and wheel inspection). UNCERTAIN: not executed on real Windows; runtime behaviour with numpy 2.x is untested.
- **Windows trap.** `Model()` defaults to `inference_framework="tflite"`. If tflite is missing, it falls back to ONNX **only when you pass model paths**; otherwise it raises `ValueError`. Always write `Model(wakeword_models=[...onnx], inference_framework="onnx")`. The models are not in the wheel; fetch them with `openwakeword.utils.download_models()` from GitHub releases. CONFIRMED (openwakeword-0.6.0 wheel, `model.py` L108-131, `utils.py` L625+)
- **Windows feature gaps.** Speex noise suppression is Linux-only. The Silero VAD gate (`vad_threshold=0..1`) works anywhere and "can significantly reduce false-positive activations in the presence of non-speech noise". CONFIRMED (README, https://github.com/dscripka/openWakeWord)
- **CPU cost.** "a single core of a Raspberry Pi 3 can run 15-20 openWakeWord models simultaneously in real-time." CONFIRMED (README). On a modern desktop CPU, one model should be a small fraction of one core. UNCERTAIN (not measured).
- **Licensing.** The code is Apache-2.0. The pre-trained models are CC BY-NC-SA 4.0, which is fine for personal use. CONFIRMED (README)

### 1.2 Custom-model training effort
- **openWakeWord's own pipeline.** The simple Colab notebook makes a model in "<1 hour", "but the performance of the model may be low"; the detailed `automatic_model_training.ipynb` gives higher quality but "requires more development experience". It needs at least several thousand synthetic positive clips, and the bundled models used about 30,000 h of negative data. English only. CONFIRMED (README). UNCERTAIN: whether the Colab still runs in late 2026. A 2025-09 commit removed an "outdated colab link", and the training dependencies (Piper sample generator, torch, speechbrain) are Linux-first, so plan on WSL2 or Colab.
- **livekit-wakeword 0.3.0** (2026-09-30, Apache-2.0, Python >=3.11, runtime needs only numpy and onnxruntime). It is "Based on openWakeWord", "Backward compatible with openWakeWord models and library", and runs generate, augment, train, export and eval from a **single YAML config**, with the CLI command `livekit-wakeword run configs/prod.yaml`. It exports ONNX, plus optional openWakeWord-compatible TFLite. System dependencies are documented only for macOS and Ubuntu (`espeak-ng ffmpeg sox portaudio`), and SkyPilot cloud-GPU recipes are included. CONFIRMED (https://pypi.org/project/livekit-wakeword/ , https://github.com/livekit/livekit-wakeword). Windows cp312/cp313 resolution succeeded in my dry run. CONFIRMED.
  - Its README claims the conv-attention head gives "100x fewer false positives per hour than openWakeWord, while detecting 17% more wake words" on its own "hey livekit" set. UNCERTAIN: a vendor benchmark, not independently reproduced.
  - [design] Train on WSL2 with your RTX GPU, or on a cloud GPU, then copy the `.onnx` to Windows. Expect an afternoon of setup and generation; the dataset size and time per run are UNCERTAIN.
- **microWakeWord** (kahrendt) is active, with commits on 2026-07-06 and 2026-05-12, but it says "Training a model that works well is still very difficult". Its notebook "will most likely not be usable" without experimentation. CONFIRMED (https://github.com/kahrendt/microWakeWord). For inference, **pymicro-wakeword 2.5.0** (2026-09-17, Apache-2.0) ships a `py3-win_amd64` wheel, supports Python 3.8–3.13, and has built-in models such as `Model.OKAY_NABU`. CONFIRMED (https://pypi.org/project/pymicro-wakeword/). [design] Use it only if a built-in phrase suits you; it is designed for ESP32-class hardware, so its CPU cost is negligible.
- **sherpa-onnx KWS** (sherpa-onnx 2.0.1, 2026-10-27, Apache-2.0, Windows wheels cp310–cp314). It does **open-vocabulary** keyword spotting: to change keywords, "the only thing you need to do is replacing the `--keywords-file`" and running `text2token`. No training is needed. The English model `sherpa-onnx-kws-zipformer-gigaspeech-3.3M-2024-01-01` was trained on 10,000 h of GigaSpeech and is about 19 MB; per-keyword `keywords_score` and `keywords_threshold` are tunable. CONFIRMED (https://pypi.org/project/sherpa-onnx/ , https://k2-fsa.github.io/sherpa/onnx/kws/pretrained_models/index.html). UNCERTAIN: the docs publish no false-alarm-per-hour or CPU real-time-factor figures, so measure them.
- **nanowakeword 3.0.0** (2026-08-19, Apache-2.0) is a Colab-trained, multi-architecture project from a small team. CONFIRMED that it exists (https://pypi.org/project/nanowakeword/). UNCERTAIN: maturity, so it is not recommended.

### 1.3 False-trigger mitigation with an open mic to friends
The risk is mostly **your own conversational speech**: friends' voices come out of your headphones, not into your mic. [design]
1. **Phrase choice** [design]. Use 3–5 syllables with uncommon phonemes that never come up in GTNH talk. **Avoid "GregTech/Gregtec"**: you will say "GregTech" all session. Also avoid "computer", "hey", "okay" plus a common word, and tier names such as "LV/MV/HV". Examples: "Hey Archivist" or "Okay Tessera" (any made-up proper noun).
2. **Threshold and VAD gate.** Tune above the default 0.5 and enable `vad_threshold`. CONFIRMED (openWakeWord README)
3. **Custom verifier** (openWakeWord). A logistic-regression second stage trained from **at least 3 positive clips and about 10 s of non-wakeword speech per speaker**, plus optional clips of past false activations, which the doc calls "one of the most effective ways to reduce false activations". You call `openwakeword.train_custom_verifier(...)`, then `Model(custom_verifier_models=..., custom_verifier_threshold=...)`. CONFIRMED (https://github.com/dscripka/openWakeWord/blob/main/docs/custom_verifier_models.md). Caveat: it filters *other* voices; it is not a defence against your own near-misses. CONFIRMED in substance: the doc says it is focused on the target speaker and is not intended as speaker verification.
4. **ASR double-check** [design]. Keep a 2.5 s ring buffer. When the detector fires, run faster-whisper (already loaded) on the last ~2 s and accept only if the transcript fuzzy-matches the phrase. This adds about 100–300 ms on the GPU (UNCERTAIN, not measured) and removes most acoustic look-alikes.
5. **Confirmation earcon plus a cancel window** [design]. Play a short chime on wake, start the Discord mute (§2), and drop the turn silently if VAD hears no speech within about 3 s. The chime goes to your headset only and never into the mic path.
6. **"In-call strict mode"** [design]. While you are in a voice channel, raise the threshold or require the verifier. Detecting the call needs RPC voice-channel events (§2.2) or a manual toggle.

---

## 2. Coexisting with Discord open mic

### 2.1 What Discord exposes
- **Keybinds.** Desktop Discord offers: Push to Talk, **Push To Mute**, Toggle Mute, Toggle Deafen, Toggle VAD, Toggle Streamer Mode, Toggle Overlay, and Toggle Overlay Lock. CONFIRMED (https://support.discord.com/hc/en-us/articles/217083547)
- **RPC `SET_VOICE_SETTINGS`** accepts `mute` (self-mute) and `deaf` (self-deafen) booleans; "Only passed fields are updated". Discord "only support[s] a single modifier of voice settings at a time over RPC", so a Stream Deck plugin connected at the same time could conflict. The transport is IPC (`\\?\pipe\discord-ipc-{n}`) on Windows. Access: "We currently do not allow access to RPC for unapproved apps without being on the game's list of testers" (up to 50 testers). CONFIRMED (https://docs.discord.com/developers/topics/rpc)
- **The working pattern is a personal app.** Third-party tools have each user create their own Discord application (client ID, client secret, OAuth redirect `https://127.0.0.1`) and authorize it, which gives absolute **"Set Mute On / Set Mute Off / Toggle Mute"**. The Macro Deck plugin notes the RPC API is "currently in 'private beta'". CONFIRMED (https://github.com/RecklessBoon/Macro-Deck-Discord-Plugin ; the older https://github.com/fredemmott/StreamDeck-Discord uses the same approach and is archived). UNCERTAIN: how long Discord will keep allowing owner/tester RPC. Treat it as best-effort.
- **Python client.** pypresence 4.6.2 (2026-07-08). CONFIRMED (https://pypi.org/project/pypresence/). UNCERTAIN: whether its API covers `SET_VOICE_SETTINGS`/AUTHORIZE directly; raw IPC frames are simple to write if it does not.

### 2.2 Recommended wiring [design unless tagged]
- **Hold-to-talk path, which needs no simulation.** Bind the *same physical key* (for example a mouse side button GTNH doesn't use) as Discord **Push To Mute** and as the assistant's pynput hold-to-talk. Do *not* suppress the key in pynput, so Discord also sees it. Discord is muted for exactly as long as the key is held. This differs from D's warning about sharing a key with Discord *Push to Talk*: Push *To Mute* is the intended pairing with an open mic. UNCERTAIN until tested: that both hooks receive the event while GTNH has focus.
- **Wake-word path.**
  1. The wake phrase itself will reach your friends. That cannot be avoided without delaying your whole Discord mic by more than the phrase length (about 1 s), which is a bad trade.
  2. When the wake word is detected, mute through RPC (`mute:true`, an absolute state with no toggle drift).
  3. Unmute after end-of-utterance (VAD), not after TTS finishes. TTS goes to your headphones, so friends can't hear it anyway.
  4. Always unmute on a timeout or in an exception/finally path, so a crash never leaves you muted.
  - Fallback is synthesizing a Push To Mute key hold with SendInput. UNCERTAIN: no primary source confirms Discord's global keybinds accept injected input. Community reports are mixed, so test it.
- **Discord-independent gate (most robust).** Route the real mic into Voicemeeter. Discord takes Voicemeeter's virtual output as its input, the assistant reads the hardware mic directly, and the app flips the strip-to-bus routing through the Voicemeeter Remote API (`voicemeeter-api` 2.7.2, 2026-03-07, MIT). CONFIRMED that the package exists (https://pypi.org/project/voicemeeter-api/). UNCERTAIN: added latency and the stability of Voicemeeter alongside the headset's ChatMix endpoints.
- **Do not** mute the Windows capture endpoint (pycaw `SetMute`). It mutes every app's capture, including the assistant's STT. UNCERTAIN (Windows endpoint-volume behaviour from memory, not re-verified this session).

### 2.3 Keeping the assistant's TTS out of the mic
- Use headphones, and send TTS only to a headset render endpoint (the Chat side of ChatMix, per D). Never send it to a virtual cable, Voicemeeter bus or "Listen to this device" that feeds Discord's input. [design]
- If speakers are ever used, make the assistant half-duplex (ignore mic and wake word while TTS plays), or the TTS will both reach Discord and re-trigger the assistant. [design]
- Discord's "Attenuation" setting (ducking other apps when someone speaks) and Windows' Communications ducking can lower TTS volume during calls. UNCERTAIN (UI behaviour not re-verified this session). Check both if TTS seems quiet.

---

## 3. Screen capture of Minecraft (GTNH) from Python on Windows

### 3.1 Libraries (late 2026)
| Lib | Status | Method | Notes |
|---|---|---|---|
| **dxcam 0.3.0** (2026-03-12) | Revived ("Updated 2026"); Windows wheels cp310–cp314 | DXGI Desktop Duplication (default) or WinRT Windows Graphics Capture (`pip install "dxcam[cv2,winrt]"`) | "240+fps on 1080p". Claims "Stable capture for full-screen exclusive Direct3D apps". "Start with `dxgi` for most workloads, especially one-shot grab". `grab()` returns **None** when no new frame has arrived since the last grab, so use `grab(new_frame_only=False)`. Multi-monitor via `output_idx`. CONFIRMED (https://pypi.org/project/dxcam/). The fullscreen claim is about D3D; **OpenGL is UNCERTAIN**. |
| **windows-capture 2.0.1** (2026-08-08) | Maintained; Rust core | WGC (`window_name=`, `monitor_index=`, `cursor_capture`, `draw_border`) plus an optional `DxgiDuplicationSession` | Callback/event API; per-window capture. CONFIRMED (https://pypi.org/project/windows-capture/). UNCERTAIN: whether the yellow capture border can be removed on your Windows build. |
| **mss 10.2.0** (2026-04-23) | Maintained, MIT, pure Python | GDI `BitBlt(SRCCOPY|CAPTUREBLT)` into a DIB section; calls `SetProcessDpiAwareness(2)` itself | The simplest option, with no DPI surprises. CONFIRMED (wheel source `mss/windows/gdi.py`). UNCERTAIN: may return black or stale frames when the GL window is in exclusive or independent-flip mode (GDI sees only the DWM-composited desktop). |
| bettercam 1.0.0 | Stale (2023-11-22) | DXGI | Skip. CONFIRMED (PyPI) |

All the maintained options resolved to Windows cp312/cp313 binary wheels in my dry run. CONFIRMED.

### 3.2 The fullscreen pitfall in GTNH specifically
- **lwjgl3ify** (Java 17+ path; latest commit 2026-10-30) now creates its window through SDL. Its `config/lwjgl3ify.cfg` has `window.borderless` (default **false**): "Should exclusive fullscreen mode replaced with borderless fullscreen mode". `borderlessWindowsCompatibility` (default **true**) makes the window 1 px taller. The source comment explains that NVIDIA's driver "silently switch[es] such a window to exclusive fullscreen", so the window is stretched 1 px beyond the monitor "so that the driver leaves it alone". CONFIRMED (https://github.com/GTNewHorizons/lwjgl3ify : `core/Config.java` L32/L136-145, `org/lwjglx/opengl/Display.java` ~L725)
  - [design] **Set `borderless=true`** and keep the compatibility option on. The game then stays DWM-composited, so mss, WGC and DXGI all see it, and alt-tabbing to the second-monitor dashboard does not flicker.
- **LWJGL 2 path (Java 8).** F11 fullscreen behaviour is UNCERTAIN (not verified). Prefer windowed/maximized, or capture with dxcam's DXGI backend, which reads the output after composition and flip.
- Desktop Duplication must be recreated after mode switches or fullscreen transitions (`DXGI_ERROR_ACCESS_LOST`). UNCERTAIN (from Microsoft API knowledge, not re-fetched this session). Wrap grabs in a recreate-and-retry.
- **HDR on.** Captures may look washed out or need tone-mapping. UNCERTAIN (not tested).
- **Fallback that always sees the GL framebuffer.** Send F2 (the vanilla screenshot key) and read the newest PNG from `.minecraft/screenshots`. It works in any display mode, but adds about 100–500 ms of disk I/O, posts a chat message and leaves files behind. UNCERTAIN (standard Minecraft behaviour, not re-verified this session).

### 3.3 Practical recipe [design]
Hotkey (pynput) → find the GTNH window rect with `win32gui` → `dxcam.create(output_idx=<monitor of that window>)` once at startup → `grab(region=window_client_rect, new_frame_only=False)` → downscale and JPEG/PNG-encode → Claude vision. Capture itself takes a few ms; the end-to-end time is dominated by the vision API round trip (seconds). Crop to the game window so Discord DMs and overlays on other monitors never leave your PC.

---

## 4. GTNH wiki (wiki.gtnewhorizons.com)

- **It is MediaWiki.** The `/wiki/Title` and `Template:` namespace URLs appear in search results (for example https://wiki.gtnewhorizons.com/wiki/Main_Page and https://wiki.gtnewhorizons.com/wiki/Template:FTB_Attribution). CONFIRMED (search index)
- **API availability: UNCERTAIN.**
  - From this sandbox the host is refused by the session's egress policy (CONNECT 403 on 2026-11-01, so not attempted again).
  - WebFetch to `/wiki/Main_Page` and `/wiki/Editing_Guideline` returned a site-side `client_error`. Research E could read some pages and was refused others.
  - That pattern fits an anti-bot gate in front of the wiki, but the gate is not confirmed.
  - Stock MediaWiki serves `/api.php`. Test it from your PC: `https://wiki.gtnewhorizons.com/api.php?action=query&meta=siteinfo&siprop=general|rightsinfo&format=json`. `rightsinfo` returns the licence text and URL.
- **Licence: UNCERTAIN.** I could not read the footer. The existence of `Template:FTB_Attribution` suggests some pages were imported from the old FTB wiki, which may carry that wiki's licence and attribution terms (UNCERTAIN). [design] For a private local cache used only by your assistant, the licence hardly matters; **do not redistribute** the cache or bundle it into anything shared until `rightsinfo` is checked.
- **Etiquette.** CONFIRMED (https://www.mediawiki.org/wiki/API:Etiquette): "There is no hard and fast limit on read requests". Make requests "in series rather than in parallel". "Use a descriptive User-Agent header that includes your application's name" (not a browser UA). Use `maxlag` for non-interactive jobs. Combine requests: "multiple '|'-separated titles" and generators. UNCERTAIN: whether third-party wikis' rules differ. Assume the GTNH admins' word overrides this.
- **Caching plan** [design]
  1. Do a one-time crawl with 1 request at a time and about 1 s between requests. Use `action=query&generator=allpages&prop=revisions&rvprop=content|ids&rvslots=main` with up to 50 titles per call.
  2. Store wikitext plus `revid` in SQLite FTS5, alongside the quest DB.
  3. Refresh nightly from `list=recentchanges` (changed titles only).
  4. Send `Accept-Encoding: gzip` and a UA like `GregtecOS/0.1 (personal assistant; contact: <your email>)`.
  5. If an anti-bot gate blocks scripted access, ask the wiki admins (GTNH Discord) for an API allowance or a `Special:Export` XML dump. Do not try to bypass it.

---

## Sources
- https://pypi.org/pypi/openwakeword/json ; https://github.com/dscripka/openWakeWord (README, git log of main) ; https://github.com/dscripka/openWakeWord/blob/main/docs/custom_verifier_models.md
- https://pypi.org/project/livekit-wakeword/ ; https://github.com/livekit/livekit-wakeword
- https://github.com/kahrendt/microWakeWord ; https://pypi.org/project/pymicro-wakeword/
- https://pypi.org/project/sherpa-onnx/ ; https://k2-fsa.github.io/sherpa/onnx/kws/pretrained_models/index.html
- https://pypi.org/project/nanowakeword/
- https://support.discord.com/hc/en-us/articles/217083547 ; https://docs.discord.com/developers/topics/rpc
- https://github.com/RecklessBoon/Macro-Deck-Discord-Plugin ; https://github.com/fredemmott/StreamDeck-Discord
- https://pypi.org/project/pypresence/ ; https://pypi.org/project/voicemeeter-api/
- https://pypi.org/project/dxcam/ ; https://pypi.org/project/windows-capture/ ; https://pypi.org/project/mss/ ; https://pypi.org/project/bettercam/
- https://github.com/GTNewHorizons/lwjgl3ify (Config.java, Display.java @ 9de770f, 2026-10-30)
- https://www.mediawiki.org/wiki/API:Etiquette
- https://wiki.gtnewhorizons.com/wiki/Main_Page ; https://wiki.gtnewhorizons.com/wiki/Template:FTB_Attribution (search index only)
- Local verification: `pip install --dry-run --platform win_amd64 --python-version 3.12|3.13 --only-binary=:all:` for every package above. Inspected the openwakeword-0.6.0 and mss-10.2.0 wheels.
