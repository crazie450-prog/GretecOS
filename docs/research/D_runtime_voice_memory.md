# D — Runtime, Voice & Memory stack for Gregtec OS

Prepared 2026-11-01. Scope: runtime (Claude Agent SDK), voice I/O on Windows while GTNH runs, Obsidian-based agent memory. Game-file formats and recipe export are out of scope.

**Legend.** `[C Sx]` = CONFIRMED by source Sx (URL list in §9). `[C M1]` = CONFIRMED by my own reproducible measurement (§3.2). `[U: …]` = UNCERTAIN, with the reason. Lines starting **Rec:** are design recommendations (my judgment, built on the labeled facts).

---

## 0. Bottom line

| Layer | Pick | Why |
|---|---|---|
| Process model | One Python 3.12 app (asyncio). Voice I/O + a **fast lane** run in-process. A **deep lane** (Agent SDK `ClaudeSDKClient`) stays warm beside it. | Voice needs sub-2 s replies. The Agent SDK runs a CLI subprocess with harness overhead. Splitting the two lanes keeps quick answers fast. |
| Fast-lane LLM | Messages API (`anthropic` SDK), `claude-haiku-5-5`, `thinking: disabled`, `effort: low`, streaming, prompt caching | Haiku 5.5 is the only current-generation model whose thinking can be fully turned off [C S21]. Price is $0.10/$0.50 per MTok [C S20]. |
| Deep lane | `claude-agent-sdk` (pinned ≥0.2.176) `ClaudeSDKClient`. Orchestrator: Sonnet 5.5 (`between_tools`). Subagents on Haiku 5.5, with Opus 5.5 for planning. Skills live in a vault-visible plugin folder. Tools are in-process `@tool` MCP. Guards are Python hook callbacks. Budget caps are set. | Gives you skills, subagents, hooks and sessions out of the box [C S1]. |
| Auth / billing | Claude Console **API key**. Optionally fund it with Max/Ultra monthly API credits. | The SDK docs prescribe API keys [C S1, S2]. Subscription use with the SDK is allowed today but the policy has changed twice in 2026 [C S28]. |
| STT | faster-whisper on CUDA, int8, `hotwords` from the vault glossary. Alternative: Parakeet v3 via onnx-asr on CPU. Optional cloud: Deepgram. | Mature on Windows. Vocabulary biasing matters for GTNH jargon [C S45, M-src]. |
| TTS | Kokoro-82M (kokoro-onnx), streamed sentence by sentence. Piper as a very light fallback. ElevenLabs Flash v2.5 / v4 Turbo if you accept a cloud service. | Good local quality under Apache-2.0. ElevenLabs has ~100 ms model latency [C S53, S57]. |
| Input | Hold-to-talk on a low-level hook (pynput). Wake word optional (openWakeWord). | Porcupine's free tier is gone [C S61]. Push-to-talk removes the end-of-turn wait. |
| Memory | Obsidian vault on local disk, accessed directly from Python. `INDEX.md` (≤200 lines). Atomic notes with frontmatter and terse `key: value` bodies. Tables as TSV. Bulk structured data in SQLite FTS5, with pointers from notes. | Mirrors Anthropic's index-plus-topic-files pattern [C S18, S30]. TSV/CSV are the smallest table encodings [C S80, M1]. |
| Sync | None if the vault lives on one PC. Otherwise Obsidian Sync set to "Create conflict file", or Syncthing. Never live-sync the SQLite file. | [C S88, S92, S96] |

---

## 1. Runtime — Claude Agent SDK (Python)

### 1.1 Package, platform, process model
- The SDK is "Claude Code as a library": the same tools, agent loop and context management, programmable in Python and TypeScript [C S1].
- Install with `pip install claude-agent-sdk`; needs Python ≥3.10 [C S2, S31]. Latest is **0.2.176, released 2026-10-31**, with a `win_amd64` wheel [C S31]. Wheels bundle a native Claude Code binary, so no separate CLI install is needed [C S2].
- Release cadence is fast: 0.2.172 → 0.2.176 between Oct 22 and Oct 31 [C S31]. **Rec:** pin the version and upgrade on purpose.
- Native Windows is supported. Git for Windows is optional; it provides the Bash tool, and without it the PowerShell tool is used. **Sandboxing is not supported on native Windows** (WSL2 only) [C S17].
- Mechanics: the SDK exchanges JSON lines with the CLI subprocess over stdin/stdout. Each `query()` spawns a new process; `ClaudeSDKClient.connect()` keeps one process open [C S33, third-party, Jan 2026].
- A Windows cold-start report: SDK 0.1.18 took about 80 s to the first message, and the 60 s `initialize` timeout fired. Workaround: `CLAUDE_CODE_STREAM_CLOSE_TIMEOUT` [C S32]. [U: not verified whether 0.2.x still shows this; measure on the target PC.] **Rec:** start the client once at boot and keep it warm.

### 1.2 Agent Skills (SKILL.md)
- Skills are filesystem artifacts only; there is **no programmatic registration API** [C S3].
- They are discovered through `setting_sources` (`user`, `project`): `~/.claude/skills`, `<cwd>/.claude/skills` and its parents up to the repo root, and `add_dirs` [C S3]. They can also come from the `plugins` option (a local path; manifest optional; folders `skills/`, `agents/`, `hooks/`, `commands/`, `.mcp.json`; skill names become `plugin:skill`). The SDK does not expand `~` in plugin paths [C S13].
- The `skills` option takes a list, `"all"`, or `[]`. The SDK adds the Skill tool automatically; if you pass an explicit `tools` list, include `"Skill"` [C S3]. The `init` message lists the skills that loaded [C S3]. `/name` dispatch works even for skills not in the list [C S3].
- Token cost (progressive disclosure): about 100 tokens of metadata per skill is always loaded; the body (under 5k tokens) loads only when triggered; bundled files cost nothing until read, and script code never enters context [C S25]. `name` is at most 64 characters, lowercase and hyphens only; `description` is at most 1,024 characters [C S25].
- Subagents can preload skills (`AgentDefinition.skills`, first 32 names) [C S4].
- **Rec:** with 40 skills, the always-loaded metadata is about 4k tokens and caches well. Put the plugin root in a *visible* vault folder, because Obsidian ignores paths that start with `.` [C S91]. That way you can edit SKILL.md files in Obsidian.

### 1.3 Subagents
- Three ways to define them: programmatically with `agents={name: AgentDefinition(...)}` (recommended), as files in `.claude/agents/`, or the built-in `general-purpose` agent [C S4].
- `AgentDefinition` fields: `description`, `prompt`, `tools`, `disallowedTools`, `model` (`fable|opus|sonnet|haiku|inherit` or a full ID), `skills`, `memory`, `mcpServers`, `maxTurns`, `background`, `effort`, `permissionMode`. `autoCompactWindow` and `omitClaudeMd` are TypeScript-only [C S4].
- Each subagent starts with a fresh context. Only the Agent-tool prompt is passed in, and only its final message comes back. By default it runs in the background (`async_launched`) [C S4].
- Caps: nesting depth defaults to 3 (`CLAUDE_CODE_MAX_SUBAGENT_SPAWN_DEPTH`), concurrency to 20 (`CLAUDE_CODE_MAX_CONCURRENT_SUBAGENTS`), and spend is uncapped unless you set `max_budget_usd` [C S4].
- Token-level streaming is **not forwarded from subagents**; `StreamEvent.parent_tool_use_id` is always None [C S8]. Only the main agent can feed TTS token by token.
- The `Workflow` tool (for orchestrating dozens to hundreds of agents) is TypeScript-only [C S4].

### 1.4 Custom tools / in-process MCP
- Define tools with `@tool(name, desc, schema)` and wrap them with `create_sdk_mcp_server(...)`; they run in-process. Tool names follow `mcp__<server>__<tool>` [C S5].
- Tool search is on by default: SDK MCP tools are deferred, so Claude sees only their names until it needs them [C S5]. Keeping a tool always loaded (`alwaysLoad`) is TypeScript-only [C S5]. [U: an extra ToolSearch round-trip adds latency; matters only on the deep lane.]
- `readOnlyHint=True` allows parallel calls. Return `is_error` to control the error text Claude sees. Python limits: `structuredContent` and audio blocks are dropped from in-process tool results, and `permission_policy` is TypeScript-only [C S5].
- `tools=[]` removes every built-in tool, leaving only your MCP tools [C S5].

### 1.5 Hooks
- Available in Python: `PreToolUse`, `PostToolUse`, `PostToolUseFailure`, `UserPromptSubmit`, `Stop`, `SubagentStart`, `SubagentStop`, `PreCompact`, `PermissionRequest`, `Notification`. TypeScript only: `SessionStart`/`SessionEnd`, `PostCompact`, `PostToolBatch` and others [C S6].
- Outputs: `permissionDecision` (allow/deny/ask/defer), `updatedInput`, `additionalContext`, `updatedToolOutput`, `systemMessage`. `{"async_": True}` makes a hook fire-and-forget [C S6].
- Default timeout is 600 s, or 30 s for `UserPromptSubmit` [C S6]. Matchers match tool names only, so check paths inside the callback, and handle Windows backslash paths [C S6].
- **Rec:** use `PreToolUse` as a path guard so Write/Edit can only touch agent-owned vault folders. Use `UserPromptSubmit` to add a small live game-state snapshot as `additionalContext`. Use `Stop` to append to the session log.

### 1.6 Sessions and persistence
- Transcripts are written automatically to `~/.claude/projects/<encoded-cwd>/*.jsonl`. You can continue, resume (by ID) or fork a session. `ClaudeSDKClient` continues the same session automatically [C S7].
- Python can suppress transcript writes with `CLAUDE_CODE_SKIP_PROMPT_HISTORY`. A `SessionStore` adapter mirrors transcripts elsewhere. `list_sessions`, `get_session_messages`, `tag_session` and `rename_session` are available [C S7].
- The docs say capturing results as application state and passing them into a fresh session is "often more robust" than shipping transcripts around [C S7].
- On a resumed session, a changed system prompt is not seen until the session is compacted, because the prompt is snapshotted [C S10].

### 1.7 Streaming and interrupts
- `include_partial_messages=True` yields `StreamEvent`s carrying `text_delta` chunks [C S8].
- Streaming-input mode is the recommended mode. `ClaudeSDKClient` supports `interrupt()`, `set_model()`, `set_permission_mode()` and `get_context_usage()`; `query()` does not support interrupts [C S9, S12].

### 1.8 System prompt, context noise, caching
- If `system_prompt` is not set, the SDK uses a **minimal** prompt. Alternatives are the `claude_code` preset or a custom string. For non-coding agents the docs recommend a custom prompt, and they recommend adding a sentence explaining what system reminders are [C S10].
- A Python string prompt is passed as one command-line argument, and Windows caps the whole command line at about 32 KB. Use `{"type": "file", "path": …}` for large prompts [C S10, S12].
- Claude Code injects context outside the system prompt: CLAUDE.md, the skill list, the subagent list, file-change notes. You can trim it with `CLAUDE_CODE_DISABLE_CLAUDE_MDS=1`, `CLAUDE_CODE_DISABLE_ATTACHMENTS=1`, and `includeGitInstructions: false` [C S10].
- Prompt caching is automatic in the SDK. With an API key the default TTL is 5 minutes; `ENABLE_PROMPT_CACHING_1H` or `CLAUDE_CODE_PROMPT_CACHE_TTL` select 1 hour. On a subscription, within the plan's included usage, your own turns get the 1-hour TTL by default [C S11]. `total_cost_usd` is a client-side estimate [C S11].
- Claude Code's own **auto memory** is on by default. Its `MEMORY.md` index (first 200 lines or 25 KB) loads every session. Turn it off with `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` or move it with `autoMemoryDirectory` [C S18]. [U: whether SDK sessions with a custom prompt load the auto-memory section. The docs place it inside the preset prompt [C S10]. Check with `get_context_usage()`.] **Rec:** disable it, or point it into the vault, so there is no second hidden memory store.

### 1.9 Authentication, billing, terms
Facts:
- SDK docs: "Unless previously approved, Anthropic does not allow third party developers to offer claude.ai login or rate limits for their products, including agents built on the Claude Agent SDK. Use the API key authentication methods…" [C S1, S2].
- Legal page: OAuth is "designed to support ordinary use of Claude Code and other native Anthropic applications". Developers building products with the Agent SDK "should use API key authentication". Developers may not route requests through Free/Pro/Max credentials on behalf of their users. Also: "Advertised usage limits for Pro and Max plans assume ordinary, individual usage of Claude Code and the Agent SDK" [C S16].
- Help Center article, last updated Oct 2026: when you are signed in with a plan, "the Agent SDK and `claude -p` still draw from your subscription limits". On June 15, 2026 Anthropic paused "previously-announced changes to Claude Agent SDK usage" [C S28]. A third-party write-up says the paused plan would have moved SDK/`-p` usage onto a separate monthly credit [C S34; U: not verified at a primary source].
- Mechanics: `claude setup-token` produces a 1-year OAuth token for `CLAUDE_CODE_OAUTH_TOKEN`. It needs a Pro/Max/Team/Enterprise plan and can only make model requests. `--bare` mode ignores OAuth and needs an API key [C S15, S14].
- Monthly API credits: Max **$100**, Ultra **$200**, Team pooled up to $500. They cover the Claude API, Managed Agents and **the Agent SDK**, but **not Claude Code**. They do not roll over. Pro, Free and Enterprise are not eligible. Use workspace spend limits to cap a project [C S27].
- Consumer Terms (effective Oct 8, 2025) bar automated or non-human access "except when … via an Anthropic API Key or where we otherwise explicitly permit it" [C S29]. Agent SDK use is governed by the Commercial Terms [C S1].

Assessment: [U: Personal, single-user SDK use on your own plan appears permitted today. S28 treats it as normal and S16 frames limits around "ordinary, individual usage". But an always-on assistant may stretch "ordinary", and the policy changed twice in 2026.] [U: the fast lane calls the Messages API directly, which requires an API key. This is an inference from S16: OAuth is for Claude Code and native apps.]
**Rec:** use one Console API key for both lanes. If you have Max or Ultra, claim the monthly credits and set a workspace spend cap.
Branding: "Gregtec OS — Powered by Claude" is fine. Calling it "Claude Code" is not allowed [C S1].

### 1.10 Current models and prices (per million tokens) [C S19, S20]
| Tier | API ID | Input / Output | Cache read | Notes |
|---|---|---|---|---|
| Haiku 5.5 | `claude-haiku-5-5` | $0.10 / $0.50 (prompt ≤100k); $0.50 / $2.50 (prompt >100k) | $0.01 ($0.05 >100k) | Fastest. Default effort `medium`. Thinking can be disabled at effort ≤ high [C S21]. |
| Sonnet 5.5 | `claude-sonnet-5-5` | $2 / $10 | $0.10 | Default effort `high`. Rejects `disabled`; `between_tools` is its lowest thinking setting [C S21]. |
| Opus 5.5 | `claude-opus-5-5` | $4 / $20 | $0.20 | Default `medium`. Thinking always on. Fast mode $8/$40: up to 2.5× output tokens/s, no TTFT gain [C S23]. |
| Fable 5.1 (top) | `claude-fable-5-1` | $10 / $50 | $0.25 | "Demanding reasoning and long-horizon agentic work". Thinking always on. |

- All four have a 1M-token context, 128K max output, and a June 2026 knowledge cutoff [C S19].
- Cache writes cost 1.25× base input (5 min) or 2× (1 h). Batch is 50% off [C S20]. Tool use adds a 286-token system prompt on the 5.5 models [C S20]. The minimum cacheable prompt is 512 tokens on the 5.5 models [C S22].
- The tokenizer used since Claude 4.7 yields about 30% more tokens for the same text [C S20].
- Legacy models still available include Haiku 4.5 ($1/$5), Sonnet 5 ($2/$10), Sonnet 4.6 ($3/$15) and Opus 4.5–5 ($5/$25) [C S20].
- The SDK accepts aliases (`haiku`, `sonnet`, `opus`, `fable`) [C S4]. [U: which concrete IDs those aliases resolve to in 0.2.176.] **Rec:** pass full IDs.

### 1.11 Why thinking settings decide voice latency
- Per-model thinking rules: Haiku 5.5 accepts `disabled` at effort ≤ high. Sonnet 5.5 accepts only `between_tools` as its lowest setting. Opus 5.5 and Fable 5.1 cannot turn thinking off [C S21].
- Artificial Analysis medians (10k-token input, 72-hour P50), time to first answer token, thinking included: Haiku 5.5 **low 7.9 s, medium 11.4 s** [C S43, S44]. Haiku 4.5 non-reasoning TTFT: **0.47–0.58 s** [C S42]. [U: Haiku 5.5 with thinking disabled has no public number; sub-second is a guess from the 4.5 figure. Measure it.]
- Docs: use Haiku 5.5 at low effort for speed [C S24]. Caching improves TTFT on long prompts. You can pre-warm the cache with `max_tokens: 0`. A cache entry becomes usable only after the first response starts [C S22].
- **Rec:** fast lane = Haiku 5.5 + `thinking: {"type":"disabled"}` + effort `low`. Fire a pre-warm request the moment PTT goes down, so the cache is warm by the time the transcript arrives.

### 1.12 Engine comparison
| | Agent SDK (Python) | `claude -p` (headless CLI) | Messages API direct | Managed Agents |
|---|---|---|---|---|
| Loop | Claude Code loop [C S1] | Same loop [C S14] | Your own loop, or the beta tool runner [C S1] | Hosted harness [C S1] |
| Process | 1 subprocess per client (persistent) or per `query()` [C S33] | New process per call; multi-turn via stream-json [C S14] | In-process HTTP | Anthropic cloud, or a self-hosted sandbox [C S1] |
| Tools / hooks | Built-ins + in-process `@tool` + Python hook callbacks [C S5, S6] | Built-ins + `--mcp-config`; hooks are shell commands in settings [C S14] | You implement them | API-configured |
| Skills / subagents | Yes [C S3, S4] | Yes. `--bare` skips skills/hooks/MCP/CLAUDE.md and is the recommended scripted mode [C S14] | Skills only via the code-execution container; no subagents [C S25] | Skills yes [C S25] |
| Interrupt | `client.interrupt()` [C S12] | SIGINT; SIGTERM leaves the turn unfinished [C S14] | Cancel the stream | — |
| System prompt default | Minimal [C S10] | Claude Code prompt [C S10] | Yours | — |
| Auth | API key (docs); plan login works [C S2, S28] | Plan or key; `--bare` needs a key [C S14] | API key | API key; $0.08 per session-hour + tokens [C S20] |
| Best use here | Deep lane | Scheduled jobs (nightly vault compaction or recap) via `claude --bare -p` | Fast voice lane | Not a fit [U: judgment — needs local files and game state; adds latency] |

### 1.13 Cost sketch [U: estimate; assumptions stated; verify with `ResultMessage.total_cost_usd` / `model_usage` [C S11]]
- **Fast lane (Haiku 5.5).** Assume 6k input tokens per turn (5k cache reads, 1k uncached) and 150 output tokens: about **$0.0002 per turn**. At 300 turns a day that is about $2 a month, plus small cache re-writes after idle gaps over 5 minutes.
- **Deep lane (Sonnet 5.5 via the SDK).** Assume 8 model calls per task, each with about 18k cache-read tokens, 2k uncached, 2k cache-write and 400 output: about **$0.12 per task**. At 15 tasks a day that is about $54 a month. Opus 5.5 is roughly twice that per task.
- The Max plan's $100/month API credit would plausibly cover a heavy month.

---

## 2. Voice on Windows (while GTNH runs)

### 2.1 Latency reference points
- Daily (Jan 2026), fully local stack on an RTX 5090: ASR 19 ms, LLM 171 ms, TTS 108 ms; **voice-to-voice P50 508 ms** (server-side; client WebRTC adds about 250 ms). That LLM was a 30B model in Q4 using 24 GB of VRAM, and turn detection fired after a 200 ms pause [C S40]. This shows what local hardware can do, not what fits beside GTNH.
- Twilio budget (Nov 2025): STT including end-of-turn 350 ms, LLM TTFT 375 ms, TTS TTFB 100 ms; **mouth-to-ear target 1,115 ms** (upper limit 1,400) [C S41].
- Push-to-talk gives an exact end-of-turn signal, so there is no silence timer to wait for. Fixed silence timers are often around 500 ms [C S41]. [U: the saving is an inference.]
- **Gregtec fast-lane estimate** [U: estimate]: STT 0.1–0.3 s + Haiku TTFT ~0.5–1.0 s + first sentence ~0.1 s + first audio 0.05–0.3 s ≈ **0.8–1.7 s**. Deep-lane tasks take seconds to minutes, so speak a short acknowledgement immediately.

### 2.2 Speech-to-text
| Option | Key facts | Fit |
|---|---|---|
| **faster-whisper** 1.2.1 (2025-10-31), MIT | CTranslate2 backend; needs CUDA 12 + cuDNN 9. Windows CUDA libraries come from Purfview's archive. Benchmark on an RTX 3070 Ti, large-v2 on 13 min of audio: fp16 4,525 MB VRAM / 63 s; int8 2,926 MB / 59 s. CPU, small model, int8: 1,477 MB RAM. Silero VAD built in; `turbo` and `distil-large-v3` supported [C S45, S46]. `transcribe()` takes `hotwords` and `initial_prompt` (checked in the 1.2.1 wheel source) [C M-src]. | **Default.** Bias it toward GTNH terms. |
| **whisper.cpp** | CUDA / Vulkan / OpenVINO backends. Memory from tiny 273 MB to large 3.9 GB. `whisper-stream` example; Silero VAD; quantization [C S47]. `pywhispercpp` 1.5.1 [C S72]. | Choose it if you want Vulkan (non-NVIDIA) or a C++ binary. |
| **Parakeet TDT 0.6B v3** | CC-BY-4.0; 25 European languages; Open ASR leaderboard average WER 6.34%; NeMo 2.4; Linux preferred [C S48]. Through **onnx-asr** (Windows, no PyTorch): RTFx 36 on a Ryzen 9800X3D CPU, 57 on a T4 (CUDA), 320 on an RTX 5070 Ti (TensorRT) [C S50]. | **CPU alternative** that saves VRAM. [U: onnx-asr advertises no hotword biasing.] |
| **Nemotron Speech Streaming EN 0.6B** (Mar 2026) | Cache-aware streaming. Chunks from 80 ms to 1.12 s; WER 8.43% → 6.93% as chunks grow. NVIDIA Open Model License; NeMo; Linux preferred [C S49]. Multilingual Nemotron 3.5 ASR Streaming 0.6B came out 2026-06-04 [C S49]. | Overkill with push-to-talk. [U: no clean Windows path.] |
| **Deepgram** (cloud) | Flux EN $0.0077/min; Nova-3 monolingual $0.0077, multilingual $0.0092; $200 starting credit [C S51]. Flux end-of-turn settings: `eot_threshold` 0.7, optional eager EOT, `eot_timeout_ms` 5000 [C S52]. | Fallback if the GPU is saturated. |

**Rec:** faster-whisper `large-v3-turbo` or `distil-large-v3`, `compute_type="int8_float16"`, `beam_size=1`, `language="en"`. Generate `hotwords` and `initial_prompt` from a vault glossary (machine, material and tier names). With push-to-talk you already know the segment boundaries, so run VAD only to trim. [U: VRAM use of turbo/distil on your GPU; measure.]

### 2.3 Text-to-speech
| Option | Key facts | Fit |
|---|---|---|
| **Kokoro-82M** | Apache-2.0 weights; v1.0 released 2025-01-27; 54 voices, 8 languages; 24 kHz [C S53]. The `kokoro` 0.9.4 package needs Python <3.13 [C S72]. `kokoro-onnx` model is ~300 MB (~80 MB quantized), MIT wrapper [C S55]. Kokoro-FastAPI measurements: GPU first chunk ~300 ms at chunk size 400; 35–100× realtime on a 4060 Ti; 3.1–4.0 GB VRAM, of which ~2.4 GB is CUDA context; CPU first chunk ~3.5 s at chunk size 200 on an older i7 [C S54]. [U: the README doesn't say whether chunk size is in characters or tokens.] | **Default.** Feed it one sentence at a time. CPU is fine if the first chunk is short. [U: measure on your CPU.] |
| **Piper** (OHF-Voice/piper1-gpl) | GPL-3.0; "fast and local"; espeak-ng phonemization. `piper-tts` 1.8.0 (2026-09-04) [C S56, S72]. | Very light fallback. [U: no latency figure verified; GPL matters only if you distribute.] |
| **ElevenLabs** (cloud) | Flash v2.5 and Eleven v4 Turbo: ~100 ms median model latency. Flash v2 ~135 ms; v3 Conversational ~165 ms. Turbo models are deprecated [C S57]. WebSocket input streaming takes LLM text as it arrives (`auto_mode`); TTFB 100–150 ms in North America. Flash has number normalization off by default [C S57, S58]. | Best latency and voice quality if you accept a paid cloud service. [U: pricing not checked.] |
| OpenAI `gpt-4o-mini-tts` | $0.60 per M text-input tokens, $12 per M audio-output tokens; the model page labels it **Deprecated** [C S59]. | Avoid. [U: successor model not identified.] |

Helper libraries: **RealtimeTTS** 0.8.10 (MIT) supports Kokoro, Piper, ElevenLabs, OpenAI, Azure, Edge, Qwen and more, and `feed()` accepts an iterator, so audio can start while text is still arriving [C S70, S72]. **RealtimeSTT** 1.1.2 (MIT) combines WebRTC + Silero VAD, a faster-whisper backend, and Porcupine/openWakeWord; needs Python ≥3.11 and <3.13 [C S69, S72]. **Pipecat** 1.15.0 (BSD-2) is a full voice pipeline framework with Anthropic, Kokoro, Piper and ElevenLabs services and a local-audio transport [C S71, S72].
**Rec:** use Python **3.12**. kokoro needs <3.13, RealtimeSTT ≥3.11 and <3.13, pipecat ≥3.11, and the Agent SDK ≥3.10 [C S72].

### 2.4 Wake word
- **Porcupine is out.** Picovoice disabled free-tier AccessKeys on **June 30, 2026**; the SDK does not run without a key, and "there is no non-commercial tier planned" [C S61; secondary source, a Home Assistant forum post quoting Picovoice].
- **openWakeWord** 0.6.0 (Feb 2024). Code is Apache-2.0; the pre-trained models are **CC BY-NC-SA 4.0** (fine for personal use). On Windows it runs on onnxruntime; Speex noise suppression is Linux-only. Targets: under 5% false rejects and under 0.5 false accepts per hour. Custom wake words can be trained in a Colab notebook [C S60]. [U: no release since Feb 2024, so treat it as unmaintained.]
- **pymicro-wakeword** (OHF-Voice) is Apache-2.0 [C S62]. [U: Windows wheels.]
- **Rec:** make push-to-talk the primary input. A wake word (for example a custom "hey gregtec") is an optional extra, and its false accepts cost both annoyance and tokens.

### 2.5 Push-to-talk hotkeys while the game has focus
- **Low-level keyboard hook facts.** The hook must live on a thread with a message loop. Windows calls it each time a key event is about to be posted to a thread's input queue. On Windows 10 1709+ the maximum timeout is **1,000 ms**, and since Windows 7 a hook that times out is **silently removed** [C S63].
- `RegisterHotKey` posts `WM_HOTKEY` on key press, and no release notification is documented, so it is **unsuitable for hold-to-talk**. It also fails if another app has already registered the combination [C S64].
- The `keyboard` library is "currently unmaintained" and warns that some games install hooks that swallow all key events [C S65]. `pynput` 1.8.2 (May 2026, LGPLv3) is maintained [C S72]. Suppressing the key on Windows needs the `win32_event_filter` workaround [C S66].
- [U: UIPI — if Prism/Minecraft runs elevated, a non-elevated hook may not see its keys. Run both at the same integrity level, normally non-admin.]
- [U: GTNH/LWJGL behaviour. Low-level hooks are system-wide, so they are expected to work in both borderless and exclusive fullscreen; test in game.]
- **Rec:**
  - Run the hook on its own thread. The callback only pushes press/release events onto a queue and returns at once.
  - Keep a 200–300 ms ring-buffer pre-roll, so the first syllable is not clipped.
  - Use a key GTNH doesn't bind, or a mouse side button (pynput also hooks the mouse); suppress it if needed.
  - Add a watchdog that re-installs the hook if it stops receiving events.
  - Don't share the key with Discord push-to-talk.

### 2.6 Audio routing on the headset
- Windows ducking: on the Communications tab, other streams are attenuated during a "communication session"; **the default is 80%** [C S67]. [U: whether opening your headset mic triggers this depends on device roles; test it, and set "Do nothing" if the game audio dips.]
- Arctis Nova Elite (the model your notes suggest): on-headset ChatMix control, a GameHub base station, and mic noise rejection of "up to 97%" [C S68]. [U: your exact model, and whether Windows shows separate Game and Chat endpoints for it.] **Rec:** send TTS to the Chat endpoint so the ChatMix dial balances assistant against game. Capture the mic as 16 kHz mono for STT.

### 2.7 Speaking before the LLM finishes; barge-in
- Building blocks: SDK `text_delta` events [C S8], RealtimeTTS `feed(iterator)` [C S70], ElevenLabs WebSocket input streaming [C S58], and Daily's sentence-boundary aggregation [C S40].
- **Rec algorithm:**
  1. Tell the model to answer in short spoken sentences, with no markdown or tables and numbers written as words. (Flash v2.5 does not normalize numbers by default [C S57].)
  2. Accumulate deltas. Flush the first chunk at the first sentence or clause boundary once ≥6 words arrive, or after 250 ms. After that, flush whole sentences.
  3. Synthesize into a playback queue.
  4. Send long outputs (tables, plans) to an Obsidian note and say only "I put the table in your notes".
- **Barge-in:** on PTT press, stop playback, clear the TTS queue, and call `client.interrupt()` if a deep-lane turn is running [C S12].

### 2.8 GPU/CPU budget next to GTNH
- Reference numbers: faster-whisper int8 ~2.9 GB VRAM (large-v2, batch test) [C S45]; Kokoro server 3.1–4.0 GB including CUDA context [C S54]; whisper.cpp large ~3.9 GB [C S47]; Parakeet needs ≥2 GB RAM [C S48].
- [U: whether faster-whisper and ONNX Runtime can share one CUDA context when they run in the same process; plausible but unverified.]
- **Rec:** start with STT on the GPU (it runs in short bursts) and TTS on the CPU (kokoro-onnx quantized, or Piper). Measure game FPS/TPS before moving TTS to the GPU. If the GPU is tight, Parakeet v3 on the CPU frees all assistant VRAM.

---

## 3. Memory — Obsidian vault as agent memory

### 3.1 Principles (from Anthropic docs)
- **Just-in-time retrieval.** Keep lightweight identifiers (file paths, stored queries, links) and load data at runtime. Folder hierarchy, naming and timestamps act as signals. Keep structured notes (a NOTES.md) outside the context window. Subagents should return condensed summaries of 1–2k tokens. Claude Code itself loads CLAUDE.md up front and fetches the rest with grep/glob [C S30].
- **Claude Code auto memory as a template.** A `MEMORY.md` index with **one line per memory**; only the first 200 lines or 25 KB load; topic files are read on demand; Claude Code nags the agent to shorten the index when it nears the limit. The guidance for CLAUDE.md is to keep each file under 200 lines [C S18].
- **Memory tool (API).** A client-side `/memories` directory. `view` truncates files longer than 16,000 characters. The docs advise capping file sizes, expiring stale files, and enforcing path-traversal checks [C S26].

### 3.2 Format token efficiency — benchmarks
- **TOON's own README** (o200k tokenizer).
  - Token counts, mixed-structure datasets: TOON 264,734 vs JSON 393,637 (−32.7%), JSON-compact 260,451 (TOON +1.6%), YAML 314,140.
  - Token counts, flat tables: **CSV 64,247 vs TOON 68,030 (+5.9%)** vs JSON 164,643.
  - Retrieval accuracy over 244 questions × 4 models, including claude-haiku-4-5: TOON 72.2%, JSON 71.4%, YAML 70.1%, JSON-compact 69.0%.
  - Accuracy on flat tables: TOON 63.1%, CSV 62.2%. For claude-haiku-4-5 alone: TOON 65.6%, JSON 63.5%, CSV 49.5%.
  - The README's own caveats: "CSV is smaller" for purely tabular data; JSON may win on deeply nested data; overlapping confidence intervals are not meaningful differences [C S80].
- **ImprovingAgents, tables** (gpt-4.1-nano, 2025-10-28): Markdown-KV 60.7% accuracy (52,104 tokens), INI 55.7% (48,100), YAML 54.7% (55,395), JSON 52.3% (66,396), Markdown-table 51.9% (25,140), **TOON 47.5% (21,518)**, CSV 44.3% (19,524) [C S81].
- **ImprovingAgents, nested data** (gpt-5-nano): YAML 62.1% (42,477 tokens), Markdown 54.3% (38,357), JSON 50.3% (57,933), TOON 43.1% (45,436) [C S82].
- **arXiv 2605.29676** (2026, 5 open-weight models, agentic tool input/output): TOON saves up to 18% of tokens at roughly a 9-point accuracy cost. Failures cascade across multi-turn parsing, and parallel tool-call output collapses for most models [C S83].
- **arXiv 2603.03306** (Feb 2026): plain JSON gives the best generation accuracy. TOON has a promising accuracy-per-token ratio for in-domain tasks, but its prompt overhead hurts short contexts [C S84].

**M1 — my measurement on GTNH-style synthetic data.**
Setup: tokenizers o200k_base and cl100k_base via `gpt-tokenizer` 4.1.0; `toon-format` 1.2.0; the TOON round-trip was verified. Scripts: `/home/claude/scripts/make_formats.py`, `/home/claude/scripts/count_tokens.mjs`; output in `/home/claude/tok/`. Each cell gives o200k tokens, then the change versus pretty JSON [C M1].

| Format | 60 machines × 8 fields | 120 stock rows × 4 fields | Small nested progress object |
|---|---|---|---|
| TSV | **1,546 (−64%)** | **1,538 (−66%)** | n/a |
| CSV | 1,568 (−63%) | 1,597 (−65%) | n/a |
| TOON (comma) | 1,648 (−61%) | 1,685 (−63%) | 147 (−31%) |
| TOON (tab) | 1,672 (−61%) | 1,627 (−64%) | — |
| Markdown table | 1,829 (−57%) | 1,994 (−56%) | — |
| key:value line per record | 2,017 (−52%) | 2,310 (−49%) | — |
| JSON compact | 2,618 (−38%) | 2,617 (−42%) | **128 (−40%)** |
| YAML | 3,161 (−25%) | 3,454 (−24%) | 141 (−34%) |
| JSON pretty | 4,241 | 4,541 | 214 |

[U: Claude's tokenizer is not public, so these proxy counts give relative guidance only. Claude 4.7+ produces about 30% more tokens than its older tokenizer [C S20]. Verify with the token-counting endpoint.]

**Conclusions.**
- Uniform tables → TSV. Tab delimiters avoid quoting commas in values; my measurement makes TSV the smallest.
- Single-entity notes → YAML frontmatter plus `key: value` lines.
- Small nested objects → compact JSON or YAML.
- Never use pretty JSON.
- Don't ask the model to *emit* TOON. [U: inference from S83/S84; on accuracy the evidence is mixed and model-dependent.]
- A Markdown table costs about 18–30% more than TSV but renders in Obsidian [C M1]. Use it only where you will read it yourself.

### 3.3 Recommended vault layout (Rec)
```
GregtecOS/                       vault root = Agent SDK cwd
  INDEX.md                       ≤200 lines, one line per note: path | type | one-line summary | updated
  glossary.tsv                   term<TAB>aliases → feeds STT hotwords and entity linking
  me/                            HUMAN-owned (agent: read-only via PreToolUse guard)
  base/                          agent-owned atomic notes (power, lines, goals, decisions), ≤~150 lines each
  log/YYYY-MM-DD.md              append-only session log (agent)
  inbox/                         agent → human proposals, one file each; human merges ("propose → review", as in PyOps)
  gos/                           local Agent SDK plugin root (visible in Obsidian)
    skills/<skill>/SKILL.md      + references/*.md, scripts/*.py
    agents/<agent>.md
  data/gos.sqlite                structured/bulk data (FTS5) — never edited by hand, never live-synced
  .claude/settings.json          hidden from Obsidian [C S91]
```
Note body style:
```
---
id: pwr-main
type: power
tier: HV
updated: 2026-11-01
src: db:power_snapshots/latest
---
eu_t_net: 3120
buffer: lapotron x2
issue: turbine 3 starved (see q:steam_flow)
```
- `src: db:<table>/<id>` and `q:<named_query>` are pointers that a `db_query` tool resolves. The note stays tiny, and the data lives in SQLite.
- One line per entry in INDEX.md, the same discipline auto memory uses [C S18].

### 3.4 Access path: filesystem vs REST API / MCP vs CLI
| Path | Facts | Verdict |
|---|---|---|
| **Direct filesystem** (Python, plus SDK Read/Grep/Glob/Edit) | Works without Obsidian running. Obsidian picks up external edits, and **merges them into unsaved editor buffers** (since 0.11.6) [C S89]. | **Primary.** Lowest latency and no tool-schema overhead. |
| Local REST API plugin | HTTPS on port 27124 with an API-key bearer token. PATCH by heading, block or frontmatter key; JsonLogic search; "open file in UI"; **built-in MCP server at `/mcp/` with 16 tools** [C S85]. [U: needs Obsidian running; implied by its being a plugin.] | Optional, e.g. "open that note on my second monitor". |
| Obsidian CLI (1.12+) | **Requires the app running**; many commands output tsv/json; enabled in Settings → General [C S86, S87]. A user report (2026-04) says `property:set` silently does nothing on externally-written files [C S90]. | Don't use it for writes to agent-written files. |
| Third-party Obsidian MCP servers | Add another process and more tool schemas. [U: judgment.] | Not needed. |

### 3.5 Sync and conflict avoidance
- **Obsidian Sync.** Markdown is merged with diff-match-patch, which "may sometimes create duplicate text". Since 1.9.7 there is a **"Create conflict file"** option, but it is set **per device**. Other file types are "last modified wins"; settings JSON is merged [C S88].
- **Syncthing.** The copy with the older modification time is renamed to `<name>.sync-conflict-<date>-<time>-<modifiedBy>.<ext>` [C S92].
- **SQLite.** Copying a database during a transaction can corrupt the copy, and the `-wal` file must travel with the database. Use `VACUUM INTO` or the backup API instead of copying the file [C S96].
- **Rec rules:**
  1. Ownership: the agent writes only in agent-owned folders, enforced by the hook. You own `me/`.
  2. Logs are append-only.
  3. The agent proposes changes to your notes through `inbox/` and never edits them directly.
  4. Write atomically (temp file, then `os.replace`). [U: atomicity on Windows holds only on the same NTFS volume.]
  5. Keep the vault in git for an undo history of agent changes.
  6. Exclude `data/*.sqlite*` from sync and ship a `VACUUM INTO` snapshot instead.

### 3.6 Large structured data → SQLite FTS5, with vault pointers
- FTS5 features: `bm25()` ranking (lower is better); external-content tables kept in sync by triggers; a `trigram` tokenizer for substring and LIKE/GLOB matching; `porter`/`unicode61` tokenizers; prefix indexes; `snippet()` and `highlight()` [C S94].
- Precedents:
  - **Basic Memory** stores Markdown files with a local SQLite index (hybrid full-text + vector search) behind MCP tools; it is AGPL-3.0 [C S93].
  - **GTNH-Claude-Skill** is a Claude skill backed by a SQLite **FTS5** database of 3,684+ GTNH quests, queried through a Java CLI. Its rule: rely on the database first and the wiki second, cite the source, and distrust the model's prior GTNH knowledge [C S95].
- **Rec:**
  - Into SQLite: inventories and stock history, machine registry, quest state, event logs, recipe and quest lookups (that data comes from the other workstream).
  - Mirror every note's text into an FTS5 table, with path, type, updated and body columns.
  - Into the vault: decisions, goals, plans, procedures (as skills) and summaries.
  - Expose one read-only `db_query` tool for named queries, plus an ad-hoc SELECT tool if wanted. Results return as TSV, capped at about 50 rows, with a "more" cursor.

---

## 4. Existing projects to borrow patterns from
| Project | Facts | Pattern to borrow |
|---|---|---|
| **PyOps** (ApocDev/pyops) | GPL-3.0. A local factory planner plus "in-game operations assistant" for Pyanodons. The app is TanStack Start + Tauri; a pure-Lua companion mod provides live research, machine and production integration. The optional OpenRouter-backed assistant "can propose production blocks for review". The repo carries AGENTS.md, CLAUDE.md, `.claude/skills/` and `.mcp.json`. 4 stars [C S100]. | The deterministic solver stays separate from the LLM. The LLM **proposes** and you **review**. A companion mod supplies live state. |
| **Wingman AI** (ShipBit) | GPL-3.0. Python core (FastAPI + WebSockets). Push-to-talk or voice activation; FasterWhisper by default, plus whispercpp. Skills are Python modules, tool-based or hook-based. Includes an MCP client. "Instant activation" runs commands from exact phrases without the LLM. YAML config per persona. Anthropic is not a listed provider [C S101]. | An instant-command path with zero LLM latency; skill packaging; per-persona config. |
| **COVAS:NEXT** (Elite Dangerous) | MIT. Reacts to game events; emulates key presses [C S102]. [U: Python backend + Electron UI, inferred from repo files.] | An event-driven "interjection" channel. Gate it by priority and cooldown (my addition). |
| **Mindcraft** | MIT. Minecraft LLM agents that support Anthropic; Java Edition up to 1.21.11, **not 1.7.10**. Warns that its code-execution mode is open to injection [C S103]. | Security posture: no free-form code execution on native Windows, which has no sandbox [C S17]. |
| **Voyager** | MIT. A skill library of executable code; automatic curriculum; self-verification. Tested on MC 1.19 [C S104]. | Promote verified procedures (for example a working AE2 setup) into skills once they succeed. |
| **GTNH-Claude-Skill** | SQLite FTS5 quest DB + CLI packaged as a skill [C S95]. | "Skill + SQLite + CLI" grounding pattern. |
| **Basic Memory** | Markdown + SQLite index, MCP tools, AGPL-3.0 [C S93]. | Index the vault; observation/relation syntax. Mind the AGPL if you copy code. |
| **Pipecat / RealtimeSTT / RealtimeTTS** | See §2.3 [C S69–S71]. | Pipeline primitives; buy rather than build the audio plumbing. |

---

## 5. Recommended architecture (Rec)
```
Mic (Arctis) → WASAPI 16 kHz mono ring buffer (300 ms pre-roll)
   ▲ PTT: pynput LL-hook thread (enqueue only) | optional openWakeWord
   │   on PTT-down: stop TTS (barge-in), fire cache pre-warm (max_tokens=0)
   ▼
STT: faster-whisper (CUDA int8, hotwords←glossary.tsv)  |  fallback: Parakeet v3 (onnx-asr CPU)
   ▼
Router
 ├─ instant commands (regex, no LLM: timers, "repeat that", "louder")      ← Wingman pattern
 └─ FAST LANE: Messages API, claude-haiku-5-5, thinking disabled, effort low, stream
      cached system = persona + INDEX.md + skill catalog (one line each)
      tools: vault_search(FTS5), db_query(read-only), note_append, timer, handoff(task)
      ├─ answer → sentence chunker → Kokoro/Piper (or ElevenLabs WS) → Chat endpoint
      └─ handoff → spoken ack → DEEP LANE
DEEP LANE: ClaudeSDKClient (warm), custom system prompt (file form), tools restricted
   orchestrator claude-sonnet-5-5 (between_tools) ; subagents: vault-curator & db-analyst (haiku-5-5),
   planner (opus-5-5) ; skills from GregtecOS/gos/ plugin ; in-proc MCP: db, notes, speak
   hooks: PreToolUse path guard · UserPromptSubmit game-state snapshot · Stop → log/
   caps: SPAWN_DEPTH=1, MAX_CONCURRENT=3, max_budget_usd per task ; auto memory off
   result → 1–3 spoken sentences + full note in vault
Scheduled: `claude --bare -p` nightly → compact logs, refresh INDEX.md, VACUUM INTO backup
Memory: Obsidian vault (direct FS) + data/gos.sqlite (FTS5) + git
```

---

## 6. Open risks
1. **Subscription policy flux.** Anthropic announced a change to SDK billing for the plan in 2026, then paused it [C S28]. The API-credit program has its own terms [C S27]. Mitigation: an API key with a workspace spend cap.
2. **SDK runtime on Windows.** It is a subprocess wrapping the CLI; a cold-start problem has been reported [C S32]; releases come fast [C S31]. Mitigation: a warm client, version pinning, a startup health check.
3. **Thinking-on defaults kill voice latency.** Opus 5.5 and Fable 5.1 can't disable thinking [C S21]. Haiku 5.5 measured 7.9–11.4 s to the first answer token with thinking on [C S43, S44]. The fast lane must explicitly disable thinking on Haiku 5.5.
4. **Haiku 5.5 price jumps fivefold above 100k prompt tokens** [C S20]. Long-running voice context must be compacted or cleared well before that.
5. **Resource contention with GTNH.** VRAM for STT/TTS includes ~2.4 GB of CUDA context per measurement [C S54]. The real impact on game FPS/TPS is unmeasured. [U]
6. **Hotkey fragility.** A hook is silently removed if its callback exceeds 1 s [C S63]; some games swallow keys [C S65]; elevation mismatch is a risk [U].
7. **Wake-word ecosystem.** Porcupine's free tier is gone [C S61]; openWakeWord has had no release since Feb 2024 [C S60].
8. **Vault races.** Obsidian auto-merges external edits into unsaved buffers [C S89]; Sync merges can duplicate text [C S88]; a CLI write bug exists [C S90]. Mitigation: the ownership partition and the inbox.
9. **Format evidence is mixed** for TOON; there are no Claude 5.x-specific accuracy benchmarks [C S80–S84; U].
10. **No sandbox on native Windows** for Claude Code tools [C S17]. Keep Bash/WebFetch off or tightly allow-listed, and guard paths with hooks.
11. **Python SDK gaps vs TypeScript.** No SessionStart/End hooks, no Workflow tool, no `structuredContent` from in-process tools, no `alwaysLoad`, no `permission_policy` [C S4–S6].

## 7. Questions only the player can answer
1. Which GPU (and how much VRAM), CPU and RAM do you have? How much VRAM and CPU does GTNH use with your settings (shaders or Angelica? render distance?)
2. Which Claude plan are you on (Pro / Max / Ultra)? Are you OK with a pay-as-you-go API key, and what monthly cap?
3. Are cloud voice services acceptable (ElevenLabs/Deepgram cost and privacy), or must voice stay local?
4. Preferred talk key (mouse side button? an unbound key?) Do you also use Discord push-to-talk? Do you want a wake word at all?
5. Exact headset model (your notes say "Arctis Elite"; Arctis Nova Elite?). Do you use SteelSeries Sonar or ChatMix? Does Windows show separate Game and Chat devices?
6. Do you edit the vault on other devices (phone, laptop)? If so, through which sync? Is there an existing vault with conventions to keep?
7. Do Prism or GTNH run as administrator? Do you play exclusive fullscreen or borderless?
8. Latency target for quick answers (say under 1.5 s)? Is "acknowledge now, answer later" acceptable for deep tasks? How talkative should it be, and should long answers go to a note or an overlay?

---

## 8. Reproducibility note
M1 can be regenerated by running `python -I /home/claude/scripts/make_formats.py` (dependencies: `toon-format`, `pyyaml`), then `node /home/claude/scripts/count_tokens.mjs /home/claude/tok/out` (dependency: `gpt-tokenizer`). M-src: `faster_whisper/transcribe.py` inside `faster_whisper-1.2.1-py3-none-any.whl` exposes the `hotwords` and `initial_prompt` parameters.

## 9. Sources
- S1 https://code.claude.com/docs/en/agent-sdk/overview
- S2 https://code.claude.com/docs/en/agent-sdk/quickstart
- S3 https://code.claude.com/docs/en/agent-sdk/skills
- S4 https://code.claude.com/docs/en/agent-sdk/subagents
- S5 https://code.claude.com/docs/en/agent-sdk/custom-tools
- S6 https://code.claude.com/docs/en/agent-sdk/hooks
- S7 https://code.claude.com/docs/en/agent-sdk/sessions
- S8 https://code.claude.com/docs/en/agent-sdk/streaming-output
- S9 https://code.claude.com/docs/en/agent-sdk/streaming-vs-single-mode
- S10 https://code.claude.com/docs/en/agent-sdk/modifying-system-prompts
- S11 https://code.claude.com/docs/en/agent-sdk/cost-tracking
- S12 https://code.claude.com/docs/en/agent-sdk/python
- S13 https://code.claude.com/docs/en/agent-sdk/plugins
- S14 https://code.claude.com/docs/en/headless
- S15 https://code.claude.com/docs/en/authentication
- S16 https://code.claude.com/docs/en/legal-and-compliance
- S17 https://code.claude.com/docs/en/setup
- S18 https://code.claude.com/docs/en/memory
- S19 https://platform.claude.com/docs/en/models/overview
- S20 https://platform.claude.com/docs/en/about-claude/pricing
- S21 https://platform.claude.com/docs/en/build-with-claude/thinking
- S22 https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- S23 https://platform.claude.com/docs/en/build-with-claude/fast-mode
- S24 https://platform.claude.com/docs/en/test-and-evaluate/strengthen-guardrails/reduce-latency
- S25 https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview
- S26 https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool
- S27 https://platform.claude.com/docs/en/about-claude/api-credits-for-subscribers
- S28 https://support.claude.com/en/articles/15036540-use-the-claude-agent-sdk-with-your-claude-plan
- S29 https://www.anthropic.com/legal/consumer-terms
- S30 https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents
- S31 https://pypi.org/project/claude-agent-sdk/ (JSON API, read 2026-11-01)
- S32 https://github.com/anthropics/claude-code/issues/16005
- S33 https://buildwithaws.substack.com/p/inside-the-claude-agent-sdk-from (third-party)
- S34 https://claudefa.st/blog/guide/development/agent-sdk-credit (third-party)
- S40 https://www.daily.co/blog/building-voice-agents-with-nvidia-open-models/
- S41 https://static0.twilio.com/en-us/blog/developers/best-practices/guide-core-latency-ai-voice-agents
- S42 https://artificialanalysis.ai/models/claude-4-5-haiku/providers
- S43 https://artificialanalysis.ai/models/claude-haiku-5-5-low/providers
- S44 https://artificialanalysis.ai/models/claude-haiku-5-5-medium/providers
- S45 https://github.com/SYSTRAN/faster-whisper
- S46 https://pypi.org/project/faster-whisper/
- S47 https://github.com/ggml-org/whisper.cpp
- S48 https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3
- S49 https://huggingface.co/nvidia/nemotron-speech-streaming-en-0.6b
- S50 https://github.com/istupakov/onnx-asr
- S51 https://deepgram.com/pricing
- S52 https://developers.deepgram.com/docs/flux/configuration
- S53 https://huggingface.co/hexgrad/Kokoro-82M
- S54 https://github.com/remsky/Kokoro-FastAPI
- S55 https://github.com/thewh1teagle/kokoro-onnx
- S56 https://github.com/OHF-Voice/piper1-gpl
- S57 https://elevenlabs.io/docs/models
- S58 https://elevenlabs.io/docs/eleven-api/guides/how-to/best-practices/latency-optimization
- S59 https://developers.openai.com/api/docs/models/gpt-4o-mini-tts
- S60 https://github.com/dscripka/openWakeWord
- S61 https://community.home-assistant.io/t/fyi-picovoice-confirmed-free-tier-accesskeys-will-stop-working-after-june-30-2026/1012744 (secondary)
- S62 https://dfsg-new-queue.debian.org/reviews/python-pymicro-wakeword
- S63 https://learn.microsoft.com/en-us/windows/win32/winmsg/lowlevelkeyboardproc
- S64 https://learn.microsoft.com/en-us/windows/win32/api/winuser/nf-winuser-registerhotkey
- S65 https://github.com/boppreh/keyboard
- S66 https://github.com/moses-palmer/pynput/issues/170
- S67 https://learn.microsoft.com/en-us/windows/win32/coreaudio/stream-attenuation
- S68 https://steelseries.com/gaming-headsets/arctis-nova-elite
- S69 https://github.com/KoljaB/RealtimeSTT
- S70 https://github.com/KoljaB/RealtimeTTS
- S71 https://github.com/pipecat-ai/pipecat
- S72 PyPI JSON metadata read 2026-11-01: pypi.org/project/{faster-whisper, openwakeword, kokoro, kokoro-onnx, piper-tts, RealtimeSTT, RealtimeTTS, pvporcupine, onnx-asr, sherpa-onnx, pywhispercpp, pipecat-ai, keyboard, pynput, nemo-toolkit}
- S80 https://github.com/toon-format/toon
- S81 https://www.improvingagents.com/blog/is-toon-good-for-table-data
- S82 https://www.improvingagents.com/blog/toon-benchmarks
- S83 https://arxiv.org/abs/2605.29676
- S84 https://arxiv.org/abs/2603.03306
- S85 https://github.com/coddingtonbear/obsidian-local-rest-api
- S86 https://obsidian.md/help/cli
- S87 https://obsidian.md/changelog/2026-02-27-desktop-v1.12.4/
- S88 https://obsidian.md/help/sync/troubleshoot
- S89 https://forum.obsidian.md/t/editor-add-a-toggle-to-disable-automatic-merging-of-changes-non-obsidian-sync/14874
- S90 https://forum.obsidian.md/t/cli-property-set-silently-succeeds-but-makes-no-change-on-externally-written-files/113166
- S91 https://www.dsebastien.net/hidden-folders-access-plugin-for-obsidian/
- S92 https://docs.syncthing.net/users/syncing.html
- S93 https://github.com/basicmachines-co/basic-memory
- S94 https://www.sqlite.org/fts5.html
- S95 https://cdn.jsdelivr.net/gh/clay07g/GTNH-Claude-Skill@main/README.md ; https://smithery.ai/skills/clay07g/gtnh-skill
- S96 https://www.sqlite.org/howtocorrupt.html
- S100 https://github.com/ApocDev/pyops
- S101 https://github.com/ShipBit/wingman-ai
- S102 https://github.com/RatherRude/Elite-Dangerous-AI-Integration
- S103 https://github.com/mindcraft-bots/mindcraft
- S104 https://github.com/MineDojo/Voyager
