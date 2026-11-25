# Plan-free buildings, the live shell designer and big bake-off cases (WO-10.17)

Design for WO-10.17. Decisions D423 (live shell designer), D424 (plan-free buildings) and D425 (big bake-off cases) are appended to `docs/decisions.md` in stage 4. All line references are at e9d48ae (main after WO-10.16, D422).

This design merges two candidate designs:

- **The base** is the minimal-change design: plan-free is site mode with no program and a floor-area NEED, and the live designer reuses the bake-off seam.
- **Grafted from the robustness/UX design:**
  - one repair-round engine shared by the bake-off and the live path;
  - an `ai` flag on the existing shell-job bodies;
  - a designer status route and the workbench's status line;
  - a code-stated block target with a hit/miss report column;
  - helper caps that count placed cells;
  - `--only` for the bake-off.
- **Errors fixed after checking the code (§8, R1-R9):**
  - the op-path `box` needs no change;
  - a per-request cap of `task_max_usd` would cut the second repair;
  - an output limit keyed on `max_blocks` would change every existing case;
  - the relief term in the plot formula was wrong;
  - shell jobs hold the planner lock for the whole job;
  - No-AI handling;
  - `Env` is frozen.

Model identifiers are deliberately left out of this document. The configured model is named only by its bake-off label, `astra`. Its provider and model id live in the existing `[[bakeoff.models]]` example in `docs/setup_guide.md`.

---

## 1. Goals and Clayton's answers

### 1.1 Clayton's request (2026-11-24, in session, verbatim)

> Perfect. I just finished the bake-off. The quality of the buildings, at least in this narrow test gap, seemed to be pretty equal between them, but Astra was way faster, way cheaper, and had way fewer errors. Just based on that, I think we'll stick with Astra for now, unless something else crazy comes along.
>
> That being said, all these buildings were around the 2,000-block limit. I'd like to see a couple options in the 5,000 to 20,000-block range, just to see what happens when things get a little bigger.
>
> Also, obviously, all of these are built around a specific machine plan. Do we have anything integrated that lets me build buildings without a machine plan on them, in case I want to just build the building and then put the machines inside it myself?

Answer to the last question today: **no**. Every shell needs 1 to 3 saved plans (`shell_store.make`, shell_store.py:138-139: "name 1 to 3 saved plans").

### 1.2 His answers to the PM's questions (as relayed in the work order; the model id is replaced by its label)

1. **Astra becomes the live shell designer in this batch.**
   - New AI shell designs and revisions go through the bake-off's tool-less designer seam (`gregtec.build.shell_brief` + `gregtec.llm.designers`). That covers the architect's `build_request`/`build_revise` and the dashboard workbench's "Ask the architect".
   - The model is named in gos.toml, e.g. the bake-off model labelled `astra`, provider openai.
   - The same gate and the same 2 repair rounds apply.
   - Spend is metered and capped like other AI spend. The key comes from keyring. No-AI mode and the credit pause are respected.
   - The planner, chat and other roles stay Claude.
   - If it is not configured, behaviour is as today (the Claude architect writes the design), so nothing breaks for anyone who hasn't configured it.
2. **Plan-free buildings are sized by interior floor area**, e.g. "at least 1,500 blocks of roofed floor".
   - GOS works out a plot (SITE) from the area.
   - It checks that the roofed, door-joined ground floor is at least that area. These are the same floor and door rules as site mode, without machines: no fit, no keep-outs, no program.
   - It exports as a schematic like any shell. Clayton places the machines himself.
3. **Big bake-off cases, both kinds:**
   - 2 big cases on existing plans with a larger plot (a site factor up to the existing 6.0 limit, machines still fitted);
   - 2 plan-free big cases.
   - Each states a block target (5,000-20,000) in the brief.
   - Why the buildings came out around 2,000 blocks: `max_blocks` is already 20,000, but SITE is sized from the machines (side ≈ sqrt(2.0 × 1.25 × keep-out floor)), so small plans get a plot of about 24×24.
4. **Plan-free is available everywhere:** the dashboard Shell workbench, the CLI, architect chat and the bake-off.

### 1.3 Success criteria

- **G1:** With `[shell] designer` unset, every existing path behaves exactly as at e9d48ae. The existing tests pass unchanged, except the bake-off case count (7 → 11).
- **G2:** With `[shell] designer = "astra"` set and ready, every new AI shell design and revision is drawn by that model through `shell_brief` + `llm.designers`:
  - the same gate as the bake-off, and at most 2 repair rounds after the first answer;
  - every try metered (lane `designer`) and held to a per-request cap and to the day cap (answered 2026-11-25, D429: the day cap only warns);
  - the version records the real cost and the designer's label, never a model id.
- **G3:** A plan-free building ("free" mode) can be designed, revised, viewed and exported from the workbench, the CLI, the architect and the bake-off. It has no plans, no program and no fit, and it is checked by the door-joined roofed-floor rule.
- **G4:** The bake-off has 4 new cases (2 on plans at site factor 4.0 and 6.0; 2 plan-free) with code-stated targets of 6,000-20,000 blocks, and its report shows hit or miss per target. Nothing about the 7 existing cases changes: the same prompt text, the same output limit and the same echo.
- **G5:** No test reaches a real vendor. Fakes only (FakeDesigner, FakeResponsesAPI on loopback, MemoryKeyring), and the outside-host guard fixture is used in every new test module.

---

## 2. The live shell designer (D423)

### 2.1 Config

A new soft table in gos.toml:

```toml
[shell]
designer = "astra"        # a [[bakeoff.models]] label (or a fake label for tests); unset = today's behaviour
designer_max_usd = 4.0    # per request, every try counted (0.5 to 20)
```

**`core/gregtec/config.py`:**
- Two new fields: `shell_designer: str = ""` and `shell_designer_max_usd: float = 4.0`.
- Both are parsed softly in `_service_keys`, like the other service keys:
  - the label must match `shell_bakeoff.LABEL` (`[a-z0-9][a-z0-9-]{0,23}`);
  - the cap must be finite and between 0.5 and 20;
  - a bad value keeps its default and its problem goes into `service_errors`.
- **config.py does not resolve the label.**
  - The promise at config.py:71-73 holds: a bad `[bakeoff]` never stops the core, and test_shell_bakeoff.py:1754 stays green.
  - The docstring note gains one sentence: the `[shell] designer` label is resolved per request by `gregtec.build.shell_designer`, so a broken `[bakeoff]` only makes the designer "not ready".
  - Add `[shell]` to the service-key table in the docstring (:3-49).

**Resolution** is done lazily, per request, by `shell_designer.resolve(cfg) -> Ready | NotReady(why)`:
1. The designer is unset → `None`. Callers take today's paths.
2. Read `shell_bakeoff.read_settings(cfg.path)`. On ConfigError/BakeoffError: NotReady("the [bakeoff] table can't be read: …").
3. The label is looked up in `settings.models`, then in `shell_bakeoff.FAKES`. Not found: NotReady("no [[bakeoff.models]] entry is labelled astra").
4. A real (non-fake) model must have a price:
   - An Anthropic model takes its price from `prices.PRICES`, as `shell_bakeoff.cost()` does.
   - An OpenAI model needs `input_usd` and `output_usd` in its entry.
   - Unpriced: NotReady("astra has no price in gos.toml: add input_usd and output_usd to its [[bakeoff.models]] entry; the live designer never runs unpriced").
   - Why: the bake-off allows unpriced models under a token cap, but an unpriced live call would be metered as $0 and would slip past the day cap and the credit alerts.
5. Key: `keys.get_key(spec.provider)`, read per request and never cached.
   - MissingKeyError → NotReady("no openai key is stored: run `gos key set openai` (the setup wizard doesn't ask for it)").
   - A keyring failure becomes NotReady with its message.
   - Fakes need no key.
6. Ready: `Ready(spec: ModelSpec, designer: llm.Designer)`, built exactly as `shell_bakeoff_cli._designers` builds it:
   - `AnthropicDesigner(anthropic_client(key), model, effort)`, or
   - `OpenAIDesigner(key, model, effort, url=OPENAI_URL)`, or
   - `FakeDesigner(label)`.

   Factor `_designers`' per-model construction into `shell_designer.build(spec, key)` so the two cannot drift.

Fake labels are allowed so the e2e and API tests can run a live path with no network. A fake designer reports provider "fake" in the status, and its cost is 0.

### 2.2 One repair-round engine: `core/gregtec/build/shell_rounds.py` (new)

Move these out of `shell_bakeoff.Runner`, so the live designer and the bake-off cannot drift:
- `_Capped` (shell_bakeoff.py:1150-1179): the guard before every try (llm.call's retries included), and `broke` on a maybe-billed error or an unmeasured reply;
- `_rounds` (:1333-1371): the first answer plus `REPAIR_ROUNDS` (2); REFUSAL; MAX_TOKENS ("the answer was cut off at the output limit (N tokens): use fewer operations"); `shell_brief.read` errors; the gate's reason; then `shell_brief.repair_brief`.

The engine's interface:
- `Capped(inner, guard: Callable[[str], None], broke: Callable[[str], None])`: the guard and broke callables take the user text.
- `rounds(ask: Callable[[str], Reply | str], brief: str, mode: str, gate: Callable[[Answer], str | None], on_refuse: Callable[[str], None], on_round: Callable[[int], None], out_tokens: int, echo: int) -> bool`. It returns True once the gate accepts an answer.

`Runner._ask`, `Runner._rounds` and `Runner._guard`/`_broke` stay where they are as the bake-off's callbacks, which keep the run record, the run caps, `_Stop` and the heartbeat. `Runner._rounds` becomes a three-line call into `shell_rounds.rounds`. The bake-off suite is the regression check: it must pass unchanged except for the case count.

### 2.3 The live designer: `core/gregtec/build/shell_designer.py` (new, about 300 lines)

**Public API:**
- `resolve(cfg)`
- `status(cfg, store, ai) -> dict`
- `held(cfg, store, meter, ai, ready) -> (why | None, cap_usd)`
- `design(ctx, req, say) -> Outcome`
- `revise(ctx, req, say) -> Outcome`

`ctx` carries cfg, store, meter, ai, home, vault and a `connect()` that opens the read-only recipe DB. `say(stage)` reports a job stage and is the cancel point (`progress.stage`).

**Pre-flight (`held`), in order.** Each failure is one sentence, and nothing is spent.
1. **Not configured:** "no shell designer is set ([shell] designer in gos.toml)".
2. **NotReady(why):** the why sentence.
3. **No-AI:**
   - `CHOICE` and `CREDITS` (no_ai.py:55-80) are honoured for every designer: "the AI is off (you turned the AI off): nothing is spent". The same applies with CREDITS ("paused for credits").
   - For an **Anthropic** designer, `ai.allowed()` decides, as for any Claude call.
   - For any other provider, `NO_KEY` ("no Anthropic key is stored") and the API problems (AUTH, CREDITS_API, NETWORK, OUTAGE) are ignored. They describe the Anthropic API, and an Anthropic outage must not stop Astra.
4. **Day cap:** `left = floor((day.cap_usd - day.spent_usd) × 100) / 100` from `CostWatch.of(store, cfg).caps()["day"]`, as `shell_bakeoff_cli._held` computes it (:360-386).
   - If `left <= 0`: "today's AI spend $10.12 has reached the day cap $10.00 ([budget] day_max_usd): the shell designer is off until tomorrow, or raise day_max_usd".
   - Otherwise the request's cap is `min(designer_max_usd, left)`.
   - This departs from D334 (the day cap is alert-only for the other lanes) the same way the bake-off already does. It is recorded in D423 and asked about in §9.
   - **Answered (D429, 2026-11-25): only alert.** The refusal and the lowered cap are gone; the guard no longer reads the day cap; D334's cost watch alerts as for every lane.
5. **One try:** if the cap is less than one try's bound (`shell_bakeoff._most(system, spec, brief, mode)`), refuse: "one try of astra can cost up to $0.97; this request's cap is $0.50: raise [shell] designer_max_usd". This runs after the brief is built, because the bound depends on it.

**The guard before every try** (retries and repairs included):
- Cancel first: `progress.check()`.
- Then, if `spent + bound(next user text) > cap`: "stopped before try 3: $1.61 spent and the next try could cost up to $1.13, over this request's cap $2.50. Nothing was saved."
- A try already sent cannot be stopped: it finishes, it is billed and metered, and the UI says "cancels before the next try".

**`broke`** (a maybe-billed error, i.e. timeout/cut_off/too_large, or an unmeasured reply):
- It adds the try's `_most` bound to `spent`.
- It records one meter row: lane `designer`, zero usage, `usd=<bound>`, task `designer:<id>:bound`.
- That keeps the day cap and the credit alerts conservative. The figure is never $0 and never unknown.

**The call:**
- `llm.call(Capped(...), shell_brief.system_prompt(mode), user, shell_brief.schema(mode), shell_brief.MAX_OUTPUT_TOKENS)`.
- The live designer uses the standard 16,000-token output limit and the standard 16,000-character echo. These are the bake-off's measured settings. The big limits (§4.4) are bake-off-only until Clayton decides (§9 Q1). **Answered (D429, 2026-11-25):** a large request (`shell_brief.big_request`: its preview, or the version it revises, over 2,000 blocks and max_blocks over 5,000) uses 32,000 tokens and the 40,000-character echo, with the cap `designer_big_max_usd` (6.0, or `designer_max_usd` when higher).

**Meter:**
- Each answered try: `meter.record("designer", spec.model, reply.usage, usd=cost(spec, usage), task=f"designer:{job or 'cli'}")`.
- The model id goes only into the meter row, exactly as the bake-off's rows do.
- The Costs view's `by_lane` and `CreditWatch` pick the new lane up with no change.

**Design flow:**
1. `say("brief")`. Under the planner lock (§2.4), open the DB and build the brief as `shell_bakeoff.prepare` does for one case:
   - the procedural preview `shell_store.make(save_it=False, mode, plans | area, site_factor, ...)`;
   - `lay` is `layout_lib.program(conn, plans).to_json(opts)` for site mode, `plan_layout` for layout mode, and `{}` for free mode;
   - then `shell_brief.design_brief(rec, lay, names_of(conn, rec), words, made.spec.theme_rules)`.

   Close the DB. Factor this into `shell_brief.prepare_design(conn, home, vault, req) -> (brief, preview)`, which both `shell_bakeoff.prepare` and the live designer call.
2. `say("asking astra")`, then `rounds(...)`. Each repair round sets the stage "repair 1 of 2" / "repair 2 of 2".
3. The gate (`say("checking")`) opens a fresh DB connection under the planner lock and calls `shell_store.make(conn, home, vault, plans, ..., ops=said.ops, mode, entrance=said.entrance, zones=said.zones, area=, site_factor=)`.
   - A refusal goes back as the repair reason.
   - On acceptance (`say("saving")`, the last cancel point): `versions.start(home, build, asked=words, by="architect", cost_usd=total, designer=label, rounds=n)`.

**Revise flow:** the same, with the brief built as `shell_bakeoff.Runner._revise` builds it:
- the saved version's record;
- `program.json` for site builds, `versions._layout_of` for layout builds, nothing for free builds;
- `shell_brief.revise_brief`.

The gate is `versions.revise(conn, home, vault, ds, words, ops=said.ops, by="architect", cost_usd=total, designer=label, rounds=n, entrance=AUTO | said.entrance, zones=said.zones)`. Factor the brief part into `shell_brief.prepare_revise(home, ds) -> brief`, shared with the bake-off.

**No SQLite handle is held across a model call.** Connections are opened for the brief and for the gate only. (Map risk 1: the 30 s tool deadline is a progress handler on the call's own connection.)

**Version record:**
- `shell_versions.start`/`revise` gain two optional fields, `designer: str | None` (the label) and `rounds: int | None`.
- `by` stays "architect", so neither the card schema nor `types.gen.ts` changes.
- A version made by the live designer has a real `cost_usd`, so the workbench's `costText` shows dollars instead of "AI (see Costs)".

**Errors:** plain sentences. Nothing is saved on any failure, and failing answers are not kept.
- `STOPS_MODEL`:
  - auth: "astra refused the openai key: run `gos key set openai`"
  - quota: "openai says astra's quota or credits are used up"
  - bad_request: "openai rejected the request: <scrubbed detail>"
- Retryables are exhausted inside `llm.call`: "astra didn't answer after 3 tries (rate limited): $0.00 spent, nothing saved".
- Three refused answers: "astra's 3 answers were all refused. Last: <reason>. $2.31 spent, nothing saved."
- **Designer errors never reach `AiMode.failed()`.** That method knows only Anthropic errors, and an Astra outage must not put every Claude role into No-AI (map risk 4).
- There is no silent fallback to Claude or to GOS's reviser. The workbench keeps its "Revise" button (GOS's own reviser), and the refusal names it.

**Outcome / job result:**
- ok: `{"design": "ds-…", "build": "sh-…", "version": 4, "designer": "astra", "rounds": 1, "cost_usd": 1.62, "tokens": {"in": 34120, "out": 27844}, "seconds": 74.0}`
- refused: `{"refused": ["…"], "designer": "astra", "rounds": 2, "cost_usd": 2.31, "attempts": [{"round": 0, "why": "…"}, …]}`

### 2.4 Shell jobs and the planner lock (a fix)

Today a `shell` job holds the planner's lock for its whole work (design_jobs.py:55-57, 251-265; only "designer" is in `OWN_LOCK`). A live designer job lasts minutes (designers TIMEOUT_S 600 per try), and holding the lock that long would block every Designer and power job.

The change:
- `OWN_LOCK = {"designer", "shell"}`.
- A helper `design_jobs.planner(lock)` is a context manager that waits for the lock in `WAIT_S` steps with `progress.check()` between them, as `_run` does now.
- Today's procedural shell work (`shell_routes.shell_job`'s `design()` and `revise()`) wraps its whole body in it, so its behaviour is unchanged.
- The AI work takes it only for the brief and for the gate, never across a model call.
- One shell job at a time still holds: kind `shell` is still `Busy`, so a procedural and an AI job never write the same design chain at once.

### 2.5 Entry points

All AI work runs as a **shell job** or in the CLI's foreground, never inside an HTTP request (map risk 10) and never inside a 30 s tool call (map risk 1).

1. **API: shell jobs gain `ai`.** `POST /api/shells/jobs` takes `{"design": {…, "ai": true}}` and `{"revise": {"design": ds, "words": …, "ai": true}}`.
   - The request is checked before any job exists. Each check below answers with the status shown and the `why` sentence:
     - `ai` must be a bool;
     - `ai: true` with the designer not configured → 409 "no shell designer is set";
     - `ai: true` with it configured but NotReady → 409 with the why;
     - a design request with `ai: true` defaults `mode` to "site" (D422: a new AI design is a site design), or takes "free" or "layout";
     - a revise request with `ai: true` needs non-empty words.
   - `held()` runs again inside the job, because the day can move between the request and the job.
   - Titles:
     - "Shell: astra designs around pl-a, pl-b"
     - "Shell: astra designs a plan-free building (≥1,500 floor)"
     - "Shell: astra revises ds-…: <words>"
   - Context factory: `shell_routes.designer_ctx(core)` uses `core.cfg`, `core.store`, `core.meter` and `core.ai`, as `plan_routes.reviewer_for` does.
2. **API: `GET /api/shells/designer`** returns `{"configured": true, "label": "astra", "provider": "openai", "ready": true, "why": null, "cap_usd": 4.0, "day_left_usd": 7.6, "ai_off": null}`.
   - Unset: `{"configured": false}`.
   - It never returns a model id.
   - It reads gos.toml's `[bakeoff]` per call, which is cheap and lazy.
3. **Workbench** (stage 3):
   - With `configured && ready`, "Ask the architect" posts a revise job with `ai: true` for the selected design and tracks it as Revise is tracked (JobProgress, Cancel).
   - A new button, "Design with the architect", posts a design job with `ai: true` from the form (plans, or the plan-free area).
   - Not configured: "Ask the architect" keeps today's `api.ask` path, and there is no new button.
   - Configured but not ready, or AI off: both buttons are disabled and the why is shown.
4. **Chat revise** (`Asker._shell_revise`, ask.py:476-523). When AI is allowed (§2.3 step 3) and the designer is ready:
   - It starts the AI revise job through `self.shell_jobs.start({"revise": {"design": ds, "words": text, "ai": true}})`.
   - There is no deep hand-off and no Claude turn, because the design and the words are known.
   - Reply: "Asked astra to revise ds-… (job j-…); the new version shows in the Shell workbench when it's done." The lane is `router.INSTANT`: GOS's code answered at once by starting a job. There is no new lane value, so `Reply.lane` (types.ts:167), voice and Discord are untouched.
   - Busy: "a shell job is already running (…): wait for it or cancel it".

   When the designer is set but not ready, the reply first states why, then hands off to the deep lane as today (so the Claude architect revises). With No-AI on (CHOICE or CREDITS), GOS's own reviser runs in code, unchanged. With the designer unset, nothing changes: the tests at test_shell_versions.py:449 and :506 stay as they are.
5. **Architect, deep lane** (new chat designs and chat revisions that reach the architect):
   - `build_request` and `build_revise` gain `ask_designer: boolean` (default false).
   - **With `ask_designer=true`:**
     - The tool validates the args as today (plans or area, mode, theme, sizes, entrance), plus `words` (default `call.request`).
     - `design`/`design_ops` must be absent.
     - It starts the AI job through `env.shell_jobs` and returns at once: "astra is drawing it as job j-…; the version shows in the Shell workbench". That is far inside `deadline_s=30.0`.
     - With no designer set: ToolError "no shell designer is set: write the design yourself (design=…)".
   - **While a designer is configured**, a hand-written `design` or `design_ops` is refused with the ToolError "a shell designer (astra) is configured: call build_request with ask_designer=true and Clayton's words; don't write the operations yourself". This is answer 1: new AI designs go through the seam.
     - GOS's own procedural design is still allowed: `save=true` with no design and no `ask_designer`.
   - The `save=false` preview head gains a line: `designer: astra (ready)`, `designer: astra (not ready: …)` or `designer: none`. The skill branches on it.
   - **Plumbing.** `Env` is a frozen dataclass (registry.py:73-87), so it gains `shell_jobs: ShellJobs | None`. `ShellJobs` is a small holder whose `start` is set late, as `deep.registry` is.
     - `ask.setup()` creates the holder and passes it through `catalog.build_registry`/`default_registry` and to the `Asker`.
     - `api/server.py` sets `holder.start` once the job registry and `AiMode` exist (after server.py:232).
     - Outside the core (`gos ask` in a terminal) `start` stays None, and the tool says "the shell designer runs in the core (gos serve)".
   - Update the texts that describe this:
     - SKILL.md §5/§5b (vault-template/_gos/skills/architect/SKILL.md:37-70): "call build_request with save=false first; if its head says a designer is ready, call it again with ask_designer=true and Clayton's words; only with `designer: none` write the design yourself";
     - the architect prompt in deep_config.py:121-130;
     - the catalog descriptions (catalog.py:505-545).
   - Cost note for D423: a chat design still pays one Claude turn to pick the inputs, plus the designer. Workbench and chat revisions pay only the designer.
   - Limitation: the architect's chat reply comes before the drawing. The outcome shows in the workbench (the job push), not in the chat thread.
6. **CLI** (shell_cli.py):
   - `gos shell design PLAN… [--mode site|layout] [--prompt WORDS] --ai [--yes]`
   - `gos shell design --mode free --area N --ai [--yes]`
   - `gos shell revise BUILD WORDS… --ai [--yes]`

   They run in the foreground. They build the brief, then print "astra · this request's cap $4.00 · one try up to $0.97 · $7.60 left today" and ask "Type yes to spend up to $4.00: " unless `--yes` is given. Then they print the stages and the outcome.

   They use the same `held`, `design` and `revise`, with `UsageMeter(store, …)`, `AiMode(store)` and `CreditWatch(meter, mode).check()` set up as `shell_bakeoff_cli._run` does (:420-474). Exit codes: 1 on a refusal, 2 when not configured or not ready.

### 2.6 Keys and docstrings
- `keys.OPTIONAL` comment (keys.py:23-26): "read by the shell bake-off and the live shell designer ([shell] designer); the setup wizard does not ask for it". test_shell_bakeoff.py:1796 still holds: OPENAI is in MANAGED and not in NAMES.
- `designers.py` module docstring (:1-2, :19-20): it is no longer only the bake-off that calls it.
- `shell_brief.py` module docstring (:3-8): a shell design now reaches a model through the deep lane only when no designer is set.

---

## 3. Plan-free buildings (D424): mode `"free"`

### 3.1 Inputs
- **mode** `"free"`. It is added to:
  - `shell_store.MODES` (:57)
  - the `shell.Spec.mode` Literal (:205)
  - `shell_brief.MODES`/`PROMPT_VERSIONS`
  - the bake-off `MODES` (via `shell_brief.MODES`)
  - the CLI `--mode` choices

  The design record has `rec["fit"]["mode"] == "free"`. Helpers `shell_store.free_mode(rec)` (next to `site_mode`, :96) and `shell_brief.free_record(rec)` (next to `site_record`, :487).
- **area**: whole columns of roofed floor, `shell.AREA = (25, 6_000)`.
- **site_factor**: optional, default `SITE_FACTOR` 2.0, within `SITE_FACTORS` (1.25, 6.0). It is also new for site mode (§4.1).
- **Unchanged:** theme, line_type, prompt, max_blocks, headroom and relief. An entrance hint is allowed.
- **Not allowed:** plans, zones and layout options.
- **Refusals** (one sentence each):
  - "a plan-free building takes an area and no plans"
  - "the area must be a whole number from 25 to 6,000 (blocks of roofed floor)"
  - "a plan-free building has no zones (zones steer where machines go)"
  - "--area is only for --mode free"
  - "name 1 to 3 saved plans" (other modes, as today)

### 3.2 SITE from the area: `shell.site_for_area(area, opts, headroom, factor) -> Box`
- side = `max(ceil(sqrt(factor × area)), ceil(sqrt(area)) + 4)`.
  - The second term guarantees room for walls on the site edge around a square floor of the area, plus slack for the door's lobby. A test pins that GOS's procedural free shell passes the floor check across the area and factor grid.
  - There is no buffer: the area already is the requirement.
  - Relief is not subtracted. The relief zone lies outside SITE (`_site_bounds`, shell.py:398-405), so the candidate design's `2·(relief+1)` term was wrong.
- height = `max(corridor_height + chase_height, SITE_HEIGHT) + headroom`. With the defaults that is 12 + headroom: 16 at headroom 4, 28 at 16.
- `SITE_MAX_SIDE = 128` (free mode only). Refusal: "6,000 blocks of floor at site factor 3.0 needs a 135-wide plot; the most is 128: lower the factor (at most 2.7 for this area) or the area".

| area | factor | plot (side × height × side) |
|---|---|---|
| 25 | 1.25 | 9 × 16 × 9 |
| 1,500 | 2.0 | 55 × 16 × 55 |
| 2,000 | 2.0 (headroom 12) | 64 × 24 × 64 |
| 3,000 | 2.0 (headroom 16) | 78 × 28 × 78 |
| 6,000 | 2.0 | 110 × 16 × 110 |

### 3.3 NEED for an area: `shell.need_for_area(area, opts)`

```
{"door_height": ch, "aisle_height": ch + chh, "cap_height": ch + chh,
 "required": area * ch, "keepout_volume": 0, "floor_area": area, "floor_required": area,
 "tallest": [0, 0, 0], "widest": [0, 0, 0]}
```

These are the keys `floor.measure` and the sandbox's `measure`/`covered()` read (floor.py:63-81, sandbox_child.py:425-454). Neither changes, so the floor/sandbox parity test (tests/test_build_floor.py) stays green.

In a script, `covered()["floor"] >= covered()["need_floor"]` is the rule's upper bound. `covered()` counts every roofed column at least door_height high; the gate counts only those joined to the door.

### 3.4 Spec, sandbox facts, procedural, id
- `shell.spec_for_area(area, site, swatches, opts, …)` is a copy of `spec_for_site` (:565-610) with `plans=()`, `program=()`, `need=need_for_area(...)`, `mode="free"` and `origin=site.lo`. It needs no machines.
- `script_facts` (:944-979): for free mode it emits `site`, `need`, `floor_y` and `area`, and no `program`. DOORWAYS and KEEP_CLEAR stay `[]`.
- `procedural(spec, fit=…)`: the assertion becomes `fit == (spec.mode in ("site", "free"))`. It makes the same building around the whole site, with the same west doorway 3 wide and `door_height` high.
- `design()` (:1321-1324): the breach assertion stays harmless, because a free spec keeps nothing clear.
- `build_id` (:1476-1521): a free branch hashes `{mode: "free", site, area, entrance}` in place of `program` (plus the usual ops, theme and palette).
- `spec_json` (:1641-1645) writes `site` and `need` for free mode too.

### 3.5 The floor check: `fit.floor_check(cells, site, area, opts, hint=None) -> Floored`

`Floored(ok, reasons, stats, door, entrance)`. It reuses site mode's machine-free pieces exactly:
1. `floor = derive(cells, site)`
2. `tall = {c: h >= corridor_height}` (fit.py:671)
3. `doors, why = _door(floor, tall, opts, hint)` (:214-315): the same door rule, lobby and ranking
4. per door, `joined = _flood(tall, d.lobby)`. Take the first door in rank order with `len(joined) >= area`, else the one with the most. This is the loop at :688-695.

Plan-free has no zones, so `region = joined`.

**Refusals**, with exact numbers:
- "the roofed floor joined to the west door has 1,212 columns at least 3 high; it needs 1,500: add at least 288 (another 248 roofed columns aren't joined to the door)". The unjoined count is `len(tall) - len(joined)`.
- The existing "y 0 is solid in N columns under your roof, e.g. [x,0,z]: the floor is y 0; leave it air" hint (:704-711, reworded without "machine").
- The `_door` refusals as they are ("no doorway: …", the hint's own sentences).
- Review fixes (WO-10.17): a door's way out must also be clear past SITE's edge, through the relief zone, at y 0..door_height-1 (`_door`'s `beyond` columns; nothing else keeps a plan-free door's approach clear): "entrance at [x,z] on the {side}: its way out is blocked in the relief zone outside SITE at y 0..2: keep it clear". And the door is `corridor_width` (3) wide, as the prompt and brief say: "no doorway 3 wide: the widest ground opening at least 3 high with a straight way out, at [x,z] on the {side}, is 1 wide; widen it to 3".

**Stats:** `{"floor": n, "need_floor": area, "spare_pct": round(100*(n-area)/area), "unjoined": m, "door": {"side", "at"}, "entrance_from": "auto"|"answer"}`. `fit_line` and the bake-off read these.

**Entrance:** the box uses the :890-893 logic. The caller sets the paste origin with `replace(spec, origin=entrance.lo, origin_entrance="e1", origin_side=door.side)`. There is no `fitted_spec` and no clear volumes.

### 3.6 The store (shell_store.py)

**`make(plan_ids, …, area=None, site_factor=None)`:**
- free mode needs `area` and no plans; the other modes need 1-3 plans and no area;
- `site_factor` is accepted for site and free (and refused for layout);
- for site mode it is passed to `_make_site` → `site_of(prog, opts, headroom, factor)` (:263).

**`_make_area(...)`**, modelled on `_make_site` (:242-328):
1. `site_for_area`
2. `spec_for_area`
3. the procedural ops when `ops` is None
4. `compile_ops` once
5. the first gate (palette, outside share, max_blocks)
6. `floor_check`
7. the spec with the origin
8. `save(home, made, {}, None)`

**Record:**
- `rec["fit"] = {"mode": "free", "site", "need", "area", "site_factor", "entrance", "zones": [], "buffer": 0, "fit": stats}`
- `rec["plans"] = []`, `rec["layout"] = ""`
- layout.json is `{}`, and there is no program.json.

**`notes` / `fit_line`:** "roofed floor 1,604 of ≥1,500, joined to the west door (spare 7%)".

**Free branches in `rebuild` (:537), `cells_of` (:593) and `check_layout` (:851):**
- compile, then re-run `floor_check` from design.json alone (`rec["fit"]["site"]`, `["area"]`, `["entrance"]`);
- refuse if the door differs from the saved `stats["door"]` (replay drift, as `_site_cells` checks its layout id);
- set the origin.

They never call `_program`, `_site_cells`, `_refit` or `_check_site`. Audit every one of the 13 `site_mode`/`site_record` call sites: each either gains a free branch or must not see a free record.

**Unchanged:** `export` (:935-959) writes a schematic like any shell, with its WEOffset at the door's entrance cell. `card()` has `ref` None, which works today.

**`summary`:** plans `[]`. The list text becomes "plan-free · ≥1,500 floor".

### 3.7 Versions (shell_versions.py)
- **`revise`** (:408-616) gets a third branch.
  - Spec: `spec_for_area(rec.fit.area, _site_box(rec, hr), …)`, where `_site_box` is machine-free (:629-634).
  - Gate: `floor_check`. Saved with `lay={}`.
  - The entrance may be AUTO or a hint; zones are refused. The area and the plot are kept: AREA is a minimum, so "more floor" fits inside the plot, and a different area means a new design.
  - GOS's procedural reviser works through the same branch.
- **`refit`** (:723-829): "sh-… is plan-free: it has no machines to refit. Place them in the game yourself, or design on plans."
- **`viewer`** (:884-965): no ghosts (`lay.get(...)` on `{}`; a test pins it).

### 3.8 Brief and prompt (shell_brief.py, prompt v7)
- `PROMPT_VERSIONS = {"layout": 5, "site": 6, "free": 7}`, and `PROMPT_VERSION` follows the newest. The layout and site texts do not change (§4.3 leaves the prompt's sandbox sentence true), so the 7 existing cases keep their prompt versions.
- `schema("free")` = `SITE_SCHEMA`. `read(text, "free")` refuses non-empty zones.
- `free_prompt()`: `site_prompt()` (:351-419) with the machines, PROGRAM, aisle, chase, keep-outs and `ROOM` taken out. The rules:
  - "an empty building Clayton furnishes himself";
  - "y 0 is the floor: keep it air under your roof";
  - "at least AREA columns of roofed floor at least NEED.door_height high, 4-joined to the lobby of one ground doorway at least NEED.door_height high and 3 wide with a straight way out to the edge of SITE";
  - "check with covered()['floor'] (an upper bound: only the columns joined to the door count) against NEED.floor_required".

  The growing-hall fragment stays, using the floor test only. The sandbox sentence ("four times max_blocks blocks") stays.
- `FREE_API`: `SITE_API` without PROGRAM, plus `AREA`. It is appended to the tool `VOCABULARY` (agents/tools/shells.py:58-66) beside `SITE_API`.
- `_free_body(rec, names, rules)` is dispatched in `_body` (:528). It gives SITE, the bounding volume, `NEED: roofed floor >= 1500 columns at least 3 high, joined to a ground door 3 wide x 3 high`, max_blocks and the palette. It keeps the `NEED: ` prefix that `repair_brief` re-echoes (:753). There is no PROGRAM and no services.
- `facts()` (:450-484): mode, site, need and area for free. `revise_brief` treats free like site for the entrance.
- The bake-off's TARGET line (§4.2) is appended by code when a case has a target.

### 3.9 Surfaces
- **CLI:**
  - `gos shell design --mode free --area 1500 [--site-factor 2.0] [--theme …] [--prompt …] [--max-blocks …] [--headroom …] [--entrance side:x,z]`
  - `plans` becomes `nargs="*"`, validated per mode as in §3.1.
  - `gos shell design PLAN… --mode site --site-factor 4.0` also works.
  - `render`/`list` (:175, :305-319) say "plan-free, ≥1,500 floor" in place of plans and layout.
  - `gos shell refit` says the §3.7 sentence.
- **API:** the `shell_job` design body (shell_routes.py:139-190) takes `mode` ("layout" by default for the procedural path, as today; "site"; "free"), `area` and `site_factor`.
  - Plans are required unless the mode is free, and free forbids them. Each gets a 400 with the §3.1 sentence.
  - Title: "Shell: a plan-free building (≥1,500 floor)".
  - Update the docstring table (:1-31).
  - `designed()` (POST /api/shells, :70-114) gets the same fields.
- **Architect tool:** in `BUILD_REQUEST_SCHEMA`:
  - `plans` is optional (drop it from `required`, :159);
  - add `area` (int, 25-6,000) and `site_factor` (number, 1.25-6.0);
  - the mode enum comes from `shell_brief.MODES`.

  The PLAN_ID loop (:226-229) runs only when plans are given. `_free_head` sits next to `_site_head` (:191). SKILL.md: "When Clayton asks for a building with no machines, use mode=free with his floor area; if he gives none, ask him. He places the machines himself."
- **Workbench** (stage 3): see §6.3.

### 3.10 Fakes (shell_fakes.py)

`FakeDesigner` reads `mode: free` and the area from the brief's `facts:` line.
- **fake-plain:** walls on the site edge, a flat roof and a west door. It passes.
- **fake-sloppy:** a wrong first answer, fixed in repair 1.
- **fake-cramped:** a hall of about 0.7 × area. It is refused with the short-by sentence, every round.
- **fake-ornate / fake-tower:** as fake-plain, plus a tower outside the floor.

---

## 4. Big bake-off cases (D425)

### 4.1 Site factor on plans
- `site_factor` is threaded through:
  - `make` → `_make_site` → `site_of`;
  - the `Case` and the run.json case dict (`create`, :970-990);
  - `_preview`, `_design` and `blind()`;
  - the CLI, the tool and the API (§3.9).
- Replay needs nothing: the saved `rec["fit"]["site"]` is reused by `site_spec_with`/`_site_cells`.

### 4.2 Case table
- `OPTIONAL` becomes `("headroom", "relief", "palette", "layout", "mode", "site_factor", "area", "target")`, keeping the prefix rule (:447-453).
- The header gains `mode site_factor area target`. Existing rows stay as they are: `csv.DictReader` gives None for missing trailing cells, and None means the default (site, factor 2.0, no area, no target).
- `LINES` gains `"none"`. The rules, each refused with one sentence naming the case:
  - line `none` ⇔ mode `free` ⇔ area is a number;
  - site_factor is `default` or a number in `SITE_FACTORS`, for site or free only;
  - target is `default` or 1,000 to 200,000, and at most max_blocks ("big-x: target 20000 is above max_blocks 15000").
- `Case` gains `site_factor: float | None`, `area: int | None` and `target: int | None`.
- **`prepare`** (:578-638): for line none there are no plans, `lay = {}` and machines = 0. When a case has a target, code appends one line to its brief: "TARGET: about 12,000 blocks (aim for 9,600 to 14,400); max_blocks is 15,000." The case's own words carry no number, so every target is worded the same way.
- **`fit_score`** (:1108-1124) free variant: fit ok = floor_check passed; placed "-"; spare % from the floor stats; the entrance; service "-".
- **New report column `target`**, shown only when some case has one:
  - "12,840 / 12,000 hit" or "4,950 / 6,000 miss (-18% under the band)";
  - the band is ±20%.
- **`blind()`** (:1729-1760) sends mode, plot `W×H×D`, area and target. It sends nothing about the models.
- **ShellBakeoff.tsx :266:**
  - plan-free: "{theme} · plan-free, ≥2,000 floor · plot 64×24×64 · aim ~12,000 blocks (at most 15,000)";
  - on plans with a target: "{theme} · the large line · plot 37×28×37 · aim ~10,000 blocks".
- **CLI:** `gos shell bakeoff run … --only ID[,ID…]` runs only the named cases. Today there is only `--limit` and `--cases FILE`. It lets Clayton run the 4 big cases without paying for the 7 old ones again. An unknown id is refused before anything is spent.
- `MAX_CASES` (24) holds 11. test_shell_bakeoff.py:473 changes from 7 to 11.

### 4.3 The four cases

They are appended last, so a big case can never stop a run before the 7 known cases are done (a `bad_request` stops that model for the rest of the run, D378). Their themes and palettes come from the shipped rows. `expect` is "changed" and `touches` is the full op list, as in the existing rows. Layout options are empty (site cases reset them anyway, `case_options` :434-439).

| id | line | theme / line_type | mode | factor | area | headroom | target | max_blocks | plot on fixtures | rough hollow shell |
|---|---|---|---|---|---|---|---|---|---|---|
| big-foundry-hall | large | th-foundry / smelting | site | 4.0 | – | 12 | 6,000 | 7,500 | 30×24×30 (~34 a side on Clayton's plans) | ~4.5k |
| big-deco-station | large | none + deco-powerhouse palette / power | site | 6.0 | – | 16 | 10,000 | 12,500 | 37×28×37 (~42 on his plans) | ~6.5k |
| free-warehouse | none | th-marble-works / general | free | 2.0 | 2,000 | 12 | 12,000 | 15,000 | 64×24×64 | ~10k |
| free-great-hall | none | none + gothic-cathedral palette / general | free | 2.0 | 3,000 | 16 | 20,000 | 25,000 | 78×28×78 | ~15k |

The hollow-shell column is walls plus roof only. Each target is above it on purpose: the brief asks for the massing (towers, galleries, buttresses) that gets there.

Briefs (the target is appended by code):
1. **big-foundry-hall:** "a long double-height foundry hall: a clerestory of tall windows, deep buttresses along both sides, a gantry crane rail the whole length inside, a sawtooth roof and three tall round chimney stacks". Follow-up: "make the buttresses deeper".
2. **big-deco-station:** "an art deco power station: a stepped central block with tall fluted pilasters, two flanking wings, a mezzanine gallery inside and a gold-trimmed parapet, a grand entrance under a sunburst crown". Follow-up: "add a second gallery level".
3. **free-warehouse:** "an empty marble warehouse for me to fill: three parallel gabled bays, an arcade of round arches along both long sides, skylights in the roofs, a clock tower over the entrance and a covered loading dock". Follow-up: "add a fourth bay".
4. **free-great-hall:** "a cathedral-scale works hall, empty inside: a vaulted nave with flying buttresses, a mezzanine on three sides, a rose window over the door, a tall lantern tower and a bell tower at one corner". Follow-up: "add a second mezzanine level".

**What is reachable.** On Clayton's real plans the largest on-plan plot is about 42 × 28 × 42 at factor 6.0. A 20,000-block building there would fill about 40% of the volume. That is why the on-plan targets are 6,000 and 10,000, and 20,000 is tried plan-free. D425 and the runbook say so.

### 4.4 Output limit and echo for big cases (bake-off only)
- `shell_brief.out_tokens(target)`: 32,000 when a case's target is above 5,000, else 16,000 (`MAX_OUTPUT_TOKENS`). The echo is `echo(target)`: 40,000 characters (= the sandbox's `MAX_SCRIPT_CHARS`) for a big case, else 16,000 (`MAX_ECHO`).
  - These are keyed on the **target**, not on max_blocks. max_blocks defaults to 20,000, so a max_blocks rule would silently move every existing case, and every live request, to the big limits.
  - A repair brief at the big echo is at most about 5k + 40k characters, inside `MAX_BRIEF_CHARS` (60k). An answer is still at most `MAX_REPLY_CHARS` (60k).
- Both values are recorded per case in run.json (`max_output_tokens`, `echo`). They feed `_most`, `first_call`, `estimate` and the cut-off sentence.
- The report's tokens-out column then shows whether a big answer needed more than 16,000. That is the evidence for §9 Q1.
- Why: Astra used 13,184 of 16,000 tokens (thinking included) on about 2,000-block designs (shell_bakeoff.py:148-151). A big case at 16k would likely be cut off at a cost of about $1 a try, and teach nothing.

### 4.5 Limits that change for 5,000-20,000 blocks
1. **Sandbox shape helpers** (sandbox_child.py:91-221). Today `_box` refuses any helper whose box **volume** exceeds the cell cap (4 × max_blocks), even `ring`, hollow `box`, `clear` and `pyramid`, which place far fewer cells. Measured: `ring((0,0,0),(99,20,99))` is refused at max_blocks 20,000. free-great-hall's plot is 78×28×78 = 170k against a 100k cap, so the prompt's own growing-hall fragment (`clear((0,0,0),(x1,top,z1))`) would be refused. The new rule:
   - `fill` and solid `box` keep the volume check: they place every cell.
   - `ring` checks `2·(dx+dz)·dy`, and hollow `box` its face count, against the cap. Both iterate only what they place. Today hollow `box` walks the whole volume; it will walk only its faces.
   - `pyramid` checks its own course count.
   - `clear(lo, hi)` places nothing, so it has no cap. It walks the box when the volume is at most `len(cells)`, and otherwise the placed cells (`[c for c in cells if inside]`). Either way that is O(min(volume, placed)) ≤ O(cap).
   - `_put` still refuses past the cap (:76-82). The prompt sentence "may place at most four times max_blocks blocks while it runs" stays true, so no prompt version changes.
   - **The op path needs no change.** `_Builder.box` (shell.py:757-775) clamps to the bounds and caps only fills; a clearing op walks at most the clamped plot volume. Both candidate designs proposed mirroring the change there; that is not needed.
   - Windows is unchanged: the Job Object limits are untouched, and no new process or API is used.
2. **max_blocks:** LIMITS is unchanged (20,000 default, at most 200,000). The cases set max_blocks = 1.25 × target.
3. **Headroom:** the cap of 16 is unchanged, so plots are at most 28 high.
4. **Unchanged and measured fine** (bake map §2):
   - compile + gate + fit at 15k cells: 1.4 s;
   - viewer: 0.86 s a view (`MAX_VIEW_CELLS` 60k);
   - schematic: 0.08 s;
   - sandbox: 80k cells in 0.42 s.
   - Known and documented, not fixed: the dashboard's 3D view turns at roughly 3-6 fps at 20k blocks (one SVG polygon per face).

### 4.6 Cost of the big cases

The block count never enters `estimate` (:694-799). The big cases cost more because of the output limit and the echo, not because of their size.

With Astra at $10 in / $50 out per million (setup_guide.md:1836), the site/free system text is about 9.4k bytes and the overhead 3.2k (measured):

| | one try | likely per step | worst per step (3 tries) | 4 big cases × 2 steps |
|---|---|---|---|---|
| standard (16k out, 16k echo) | ≈ $0.97 | ≈ $0.81 | ≈ $3.2 | – |
| big (32k out, 40k echo) | ≈ $1.77 | ≈ $1.45 | ≈ $6.1 | likely ≈ $12, worst ≈ $49 |

- The bake-off lowers its cap to what the **day cap** leaves (`_run` :466-474). At the default `day_max_usd = 10` it would stop partway through the big cases.
- The runbook says: run them with `--only big-foundry-hall,big-deco-station,free-warehouse,free-great-hall`, set `cap_usd` from the printed worst case, and raise `[budget] day_max_usd` for that day (or split the run over two days).
- The CLI's existing per-case estimate lines show each big case's share.

---

## 5. Limits and numbers (one table)

| Item | Value | Where | Status |
|---|---|---|---|
| Live designer per-request cap | `[shell] designer_max_usd`, default 4.0, 0.5-20 | config.py | new (D423) |
| Why 4.0 | worst request ≈ 0.97 + 1.13 + 1.13 = $3.23 at 16k out; a $2 cap (`task_max_usd`) stops before the 2nd repair (spent ≈ $1.5 + next ≈ $1.13 > $2) | §2.3 | — |
| Live output limit / echo | 16,000 tokens / 16,000 chars | shell_brief | unchanged (D429: 32,000 / 40,000 for a large request) |
| Bake-off big-case output / echo | 32,000 / 40,000 when target > 5,000 | shell_brief.out_tokens/echo | new (D425) |
| Repair rounds | 2 after the first answer | shell_rounds (moved) | unchanged |
| Plan-free area | 25 to 6,000 columns | shell.AREA | new (D424) |
| Plan-free plot side | max(ceil(sqrt(f·A)), ceil(sqrt(A)) + 4), at most 128 | shell.site_for_area | new |
| Plan-free plot height | max(ch+chh, 12) + headroom (16 default, 28 max) | shell.site_for_area | new |
| Site factor | 1.25-6.0, default 2.0 (site and free) | shell.SITE_FACTORS | existing, now plumbed |
| Helper caps | placed cells ≤ 4 × max_blocks; clear has no cap | sandbox_child | changed (D425) |
| Day cap | refuses designer requests at 0 left | shell_designer.held | new behaviour (D423, §9 Q2); D429: only alerts |
| Meter lane | `designer`, task `designer:<job>` (`:bound` for unmeasured tries) | usage | new |
| Shell job lock | planner lock only around non-model steps | design_jobs | changed |
| Bake-off cases | 11 (MAX_CASES 24) | tsv | +4 |

---

## 6. Test plan

No test reaches a vendor. Every new module reuses the outside-host guard fixture (test_shell_bakeoff.py:3116), `MemoryKeyring` (:152), `FakeDesigner` and `FakeResponsesAPI` on loopback. Gates: `uv run ruff check .`, `uv run ruff format --check .`, `uv run pytest -q -n 4 --dist loadfile -p no:warnings`, and `pnpm -C dashboard build`/`test`/`e2e` (the Playwright chromium at /opt/pw-browsers/chromium).

### 6.1 Plan-free core (stage 1)

**test_build_fit.py, `floor_check`:**
- pass;
- short, with the exact sentence and the unjoined count;
- y-0 solid hint;
- no door;
- the hint is honoured, and a bad hint gets its sentence;
- the door ranking equals `fit`'s for the same cells;
- the entrance box;
- a porch door loses to the hall door (the :688-695 rule).

**test_build_floor.py:** `need_for_area` through `floor.measure` equals `sandbox_child.measure` (parity).

**test_shell.py:**
- `site_for_area` numbers (the §3.2 table); the max-side refusal; the area and factor bounds.
- The procedural free shell passes `floor_check` for area ∈ {25, 100, 1,500, 6,000} × factor ∈ {1.25, 2.0, max allowed}, with max_blocks raised where the plot needs it.
- `build_id` is stable and differs by area and by site.
- `script_facts` for free mode has no program.
- Sandbox helpers:
  - ring 100×28×100 passes at max_blocks 20,000;
  - hollow box 78×28×78 passes at 25,000;
  - fill over the cap is still refused;
  - clear over a 170k volume passes and runs in under 1 s;
  - pyramid's count;
  - `_put` still caps;
  - a helper-built design compiles the same through the sandbox before and after for the shipped fixtures.

**test_shell_store.py / test_shell_versions.py:**
- make free: saved, no program.json, layout.json `{}`, `plans: []`;
- rebuild, cells_of and check_layout without plans;
- export writes a schematic whose origin is the door cell;
- the viewer has no ghosts;
- revise free, both procedural and with ops; the area is kept; zones are refused;
- refit is refused with the exact sentence;
- plans in free mode are refused; an area in site mode is refused;
- site_factor 6.0 gives a bigger `fit.site`, and its replay is unchanged.

**test_shell_brief.py:**
- the v7 prompt has no PROGRAM, aisle or keep-out words;
- the free body has the `NEED: ` prefix, and `repair_brief` re-echoes it;
- `read(text, "free")` refuses zones;
- the layout and site prompt texts are byte-identical to e9d48ae (a golden hash).

**Tools (tests/unit/tools/test_shell_architect.py):**
- `build_request mode=free area=…` with no plans works;
- plans together with free mode are refused;
- the PLAN_ID loop is skipped;
- `_free_head`.

**API:** design jobs with mode free and area; a 400 for each §3.1 misuse; the title.

**CLI:** the arg sentences; render/list text; refit refusal.

**Integration:** test_shell_workbench_harness gets a plan-free design → export on the real catalog.

### 6.2 Live designer (stage 2)

**tests/unit/test_shell_designer.py (new):**
- `resolve`:
  - unset gives None;
  - a bad `[shell]` value goes to `service_errors`, and `config.load` still succeeds;
  - a broken `[bakeoff]` gives NotReady, and the core still starts;
  - an unknown label;
  - an unpriced openai label gives NotReady with the exact sentence;
  - a missing key, with the `gos key set openai` hint;
  - a fake label is ready with no key.
- `held`:
  - CHOICE and CREDITS refuse;
  - NO_KEY, AUTH and OUTAGE do **not** refuse for an openai designer, and do for an anthropic one;
  - day left ≤ 0 refuses with the figures;
  - the cap is lowered to the day left;
  - cap < one try refuses.
- **Guard:**
  - it stops before try N with exact figures;
  - a maybe-billed error and an unmeasured reply each record a `:bound` meter row with usd = `_most`;
  - Cancel takes effect before the next try.
- **Rounds:**
  - fake-plain passes on the first answer;
  - fake-sloppy passes in round 1, and the version has `by="architect"`, `designer="fake-sloppy"`, `rounds=1`, `cost_usd=0`;
  - fake-cramped is refused after exactly 2 repairs, with `attempts` of 3 and nothing saved;
  - the MAX_TOKENS sentence.
- **Vendor errors** (`OpenAIDesigner(url=FakeResponsesAPI)` + MemoryKeyring):
  - 401, 429 quota and 400 each give one try, no retry and the exact sentence;
  - `ai.failed` is never called (a spy);
  - a 503 is retried per `llm.call` and then refused.
- **Meter:** lane `designer`, one row per answered try, the model id only in the meter row; the version and the job result carry the label only.
- End to end: a design and a revise through FakeResponsesAPI with a price, giving `cost_usd` > 0 on the version.

**tests/unit/test_shell_rounds.py:** the extracted engine gives the same step records as before on the bake-off's `Scripted` designers. The whole bake-off suite stays green.

**Jobs:**
- `OWN_LOCK` holds shell;
- a procedural shell job still takes the planner lock for its whole work;
- an AI shell job does not hold it during the model call (a power job runs meanwhile, with a fake designer that blocks on an event);
- a second shell job is Busy.

**API:**
- `GET /api/shells/designer`: unset, ready, not ready, AI off;
- `ai: true` with the designer unset or not ready: 409 with the why, and no job created;
- an AI design job runs to a version with cost and label;
- an AI revise job;
- Cancel before a try saves nothing.

**Asker:**
- with the designer unset, the existing tests at test_shell_versions.py:449/:506 pass unchanged;
- set and ready: `ask(words, shell=ds)` starts a job, lane INSTANT, no hand-off;
- set but not ready: deep hand-off plus the why;
- No-AI CHOICE: code reviser.

**Tools:**
- `ask_designer=true` starts a job and returns in under 1 s;
- a hand-written design while a designer is set is refused;
- `ask_designer` with none set is refused;
- `shell_jobs` None gives the "runs in the core" sentence;
- the preview head's designer line;
- test_tool_registry and test_deep_lane for the schema changes.

**CLI `--ai`:** with a fake and `--yes`; with a priced fake vendor plus a typed "yes" (no `--yes`); a refusal exit code.

### 6.3 Dashboard (stage 3)

**vitest:**
- `jobBody`: free validation ("Give the floor area (25 to 6,000)"), no plans when free, the `ai` flag;
- `byText`: "AI (astra)" when `designer` is set; old versions keep "AI (see Costs)";
- `costText` with real dollars;
- the status line text.

**New Playwright spec `dashboard/e2e/shell_designer.spec.ts`**, with fake_serve/serve_core given `[shell] designer = "fake-plain"`:
- the plan-free toggle plus area → Design new → a version whose list text reads "plan-free · ≥N floor" and whose viewer shows "plan-free: no machines";
- Design with the architect → JobProgress → a version reading "AI (fake-plain)";
- Ask the architect on it → a new version;
- the not-ready state (bad label config) gives disabled buttons with the why;
- the unset config keeps today's buttons (the `shell-ask` path through `api.ask`).

**shell_bakeoff.spec.ts:** the plan-free case header.

### 6.4 Bake-off (stage 4)
- `load_cases`:
  - 11 cases;
  - each new column rule's sentence;
  - line none;
  - the existing short rows still load;
  - `--only` filters and refuses unknown ids.
- `prepare`:
  - free (no plans, `lay={}`, machines 0, the TARGET line);
  - big site (the fixture plot 37×28×37 at 6.0).
- `out_tokens`/`echo`:
  - 32k/40k only for targets above 5,000;
  - the 7 old cases keep 16k/16k (their brief, system text, `_most` and estimate lines are byte-identical: golden);
  - estimate per-case figures (golden).
- Report:
  - the target hit/miss column;
  - the free fit cells;
  - `blind()` fields.
- Fake runs of all 4 big cases end to end (test_shell_bakeoff_harness on the real catalog).

---

## 7. File-by-file plan, in 4 build stages

Each stage ends green on ruff, ruff format and pytest (and pnpm for stage 3), and is committed separately on this batch's branch.

### Stage 1: plan-free core

The floor-area rule, the site, the gate, the store, versions, schematic export and the CLI, plus the site factor on plans.

- `core/gregtec/build/shell.py`:
  - `AREA`, `SITE_MAX_SIDE`;
  - `site_for_area`, `need_for_area`, `spec_for_area`;
  - the `Spec.mode` Literal;
  - the `procedural` assertion;
  - `script_facts`, `spec_json` and `build_id` free branches.
- `core/gregtec/build/fit.py`: `floor_check`, `Floored`.
- `core/gregtec/build/sandbox_child.py`: the per-helper caps and clear-by-placed (§4.5 1).
- `core/gregtec/build/shell_store.py`:
  - `MODES`, `free_mode`;
  - `make(area=, site_factor=)`, `_make_area`, site_factor to `site_of`;
  - free branches in rebuild/cells_of/check_layout/notes/fit_line/summary;
  - the audit of the `site_mode` call sites.
- `core/gregtec/build/shell_versions.py`: the revise free branch; the refit refusal.
- `core/gregtec/build/shell_brief.py`:
  - `MODES`, `PROMPT_VERSIONS["free"]=7`;
  - `free_prompt`, `FREE_API`, `_free_body`, `free_record`;
  - `facts`, `read`, `schema`.
- `core/gregtec/build/shell_fakes.py`: free answers.
- `core/gregtec/build/shell_cli.py`: `plans nargs="*"`, `--area`, `--site-factor`, `--entrance`; render/list; the refit sentence.
- `core/gregtec/api/shell_routes.py`: the design job and `designed()` take mode/area/site_factor; the docstring table.
- `core/gregtec/agents/tools/shells.py`: plans optional, `area`, `site_factor`, mode enum; `_free_head`; `VOCABULARY` gains `FREE_API`.
- `core/gregtec/agents/tools/catalog.py`: the descriptions for free mode.
- `vault-template/_gos/skills/architect/SKILL.md`: the free-mode paragraph.
- Tests: §6.1.
- Docs:
  - `docs/runbooks/shells.md`: a plan-free section;
  - `docs/design/building_first_site_mode.md`: a pointer to this doc.

### Stage 2: live designer

Config, routing through the seam, the architect tool, the workbench API, the meter and the docs.

- `core/gregtec/config.py`: the `[shell]` keys; the docstring tables :3-49 and :71-73.
- `core/gregtec/build/shell_rounds.py` (new): `Capped`, `rounds`.
- `core/gregtec/build/shell_bakeoff.py`:
  - `_rounds`/`_Capped` delegate to shell_rounds;
  - `prepare` uses `shell_brief.prepare_design`;
  - `_revise` uses `prepare_revise`.
  
  There are no case changes yet.
- `core/gregtec/build/shell_bakeoff_cli.py`: `_designers` uses `shell_designer.build`.
- `core/gregtec/build/shell_brief.py`: `prepare_design`, `prepare_revise`; the module docstring.
- `core/gregtec/build/shell_designer.py` (new): resolve, build, status, held, design, revise; the sentences; lane `designer`.
- `core/gregtec/build/shell_versions.py`: the optional `designer` and `rounds` on start/revise.
- `core/gregtec/build/shell_cli.py`: `--ai`, `--yes`.
- `core/gregtec/planner/design_jobs.py`: `OWN_LOCK += shell`; the `planner()` helper.
- `core/gregtec/api/shell_routes.py`: the `ai` flag on the job bodies; `GET /api/shells/designer`; `designer_ctx`; procedural work wrapped in `planner()`.
- `core/gregtec/api/server.py`: set `ShellJobs.start` after the job registry and `AiMode` exist.
- `core/gregtec/agents/tools/registry.py`: `Env.shell_jobs`, the `ShellJobs` holder.
- `core/gregtec/agents/tools/catalog.py`: plumbing and descriptions.
- `core/gregtec/agents/tools/shells.py`: `ask_designer`, the refusal of hand-written ops, the job start, the preview head's designer line.
- `core/gregtec/agents/ask.py`: `_shell_revise`'s designer branch; `shell_jobs` plumbing in `setup()`.
- `core/gregtec/agents/deep_config.py`: the architect prompt (:121-130).
- `core/gregtec/llm/keys.py`: the `OPTIONAL` comment.
- `core/gregtec/llm/designers.py`: the docstring.
- `vault-template/_gos/skills/architect/SKILL.md`: §5/§5b `ask_designer`.
- Tests: §6.2.
- Docs:
  - `docs/runbooks/shells.md`: the live designer, its cap and its refusals;
  - `docs/setup_guide.md`: a `[shell] designer = "astra"` step after the bake-off section, using the existing `[[bakeoff.models]]` entry and keeping to test_setup_guide's guard.

### Stage 3: dashboard

The workbench UI for plan-free and the live designer indication, plus e2e. Components come only from the WO-2.0 design system and catalog.

- `dashboard/src/views/ShellWorkbench.tsx`:
  - a two-option mode control ("On plans" / "Plan-free"): two `gos-chip` buttons with `aria-pressed`, the pattern the view already uses for its version toggles (ShellWorkbench.tsx:250);
  - plan-free hides the plan pickers (:535-559) and shows "Interior floor (blocks)", a number field styled like max_blocks/headroom, with a note "plot about 55 × 55 at factor 2.0". The note is computed by the server: the design job's 400/preview, or a small `GET /api/shells/plot?area=&factor=` that calls `site_for_area`. The dashboard never computes the formula.
  - an Advanced "plot size factor" field for both modes;
  - the designer status line under the buttons: "Designer: astra · up to $4.00 a request · $7.60 left today", or the why;
  - "Design with the architect" and the AI path of "Ask the architect" (§2.5 3);
  - `byText`/`costText`; the ghost copy (:184-186); the design list text (:652).
- `dashboard/src/views/ShellBakeoff.tsx`: the case header (:266).
- `dashboard/src/api/types.ts`:
  - `ShellJobBody` gains mode, area, site_factor and ai;
  - `ShellVersion` gains `designer?: string | null` and `rounds?: number | null`;
  - a new `ShellDesignerStatus`.
- `dashboard/src/api/client.ts`: `shellDesigner()` (and `shellPlot()` if used).
- `dashboard/e2e/shell_designer.spec.ts` (new); `dashboard/e2e/fake_serve.py` and `serve_core.py` get the fake designer config; `shell_bakeoff.spec.ts` gets the header.
- vitest: §6.3.

### Stage 4: bake-off, fakes, report, docs and decisions

- `core/gregtec/build/shell_bakeoff.py`:
  - `OPTIONAL`, `LINES`, the `Case` fields and the loader rules;
  - `prepare` for free, and the TARGET line;
  - create/_preview/_design/_revise with area and site_factor;
  - `fit_score` free;
  - per-case out_tokens/echo in `_ask`, `_most`, `first_call` and `estimate`;
  - the target column; `blind()`.
- `core/gregtec/build/shell_brief.py`: `out_tokens(target)`, `echo(target)`, `repair_brief(…, echo=)`.
- `core/gregtec/build/shell_bakeoff_cli.py`: `--only`; area/target in the case lines.
- `tests/evals/shell_bakeoff.tsv`: the header gains `mode site_factor area target`; 4 rows appended.
- Tests: §6.4.
- Docs and records:
  - `docs/runbooks/shell_bakeoff.md`: the big cases, `--only`, the cost table (§4.6) and the day-cap note;
  - `docs/setup_guide.md`: the big-case run step, within the guard;
  - `docs/deployment_checklist.md` 5.11.17: answered **yes** (D423), plus one new row for the designer's day-cap rule (§9 Q2);
  - `docs/decisions.md`: D423, D424 and D425 appended after D422 in the table format;
  - `docs/implementation_plan.md` §8: the WO-10.17 entry;
  - `docs/design/building_first_site_mode.md`: the free-mode pointer (if not done in stage 1).

**Decision rows (gist):**
- **D423:**
  - `[shell] designer` names a priced `[[bakeoff.models]]` label. That model draws every new AI shell design and revision through `shell_brief` + `llm.designers`: workbench jobs (`ai: true`), CLI `--ai`, chat revise, and the architect's `ask_designer`. The architect may not hand-write operations while one is set.
  - Same gate, 2 repair rounds (shared engine).
  - The per-request cap is `designer_max_usd` (4.0). It refuses at the day cap (a departure from D334, as the bake-off does). (D429: large requests capped by `designer_big_max_usd`, 6.0; the day cap only alerts.)
  - Meter lane `designer`; unmeasured tries are metered at their bound.
  - CHOICE/CREDITS are honoured; Anthropic-only No-AI reasons are ignored for other providers; designer errors never trip No-AI.
  - Unset = unchanged. Shell jobs take the planner lock only around non-model steps.
  - Checklist 5.11.17: yes.
- **D424:**
  - mode `free`, sized by a floor area of 25-6,000.
  - Plot side max(ceil(sqrt(f·A)), ceil(sqrt(A))+4), at most 128; height max(ch+chh, 12)+headroom.
  - Gate: door-joined roofed floor ≥ A at corridor height, using site mode's door rule.
  - No plans, no program.json, layout `{}`; refit refused; schematic export as usual; prompt v7.
  - Available in the CLI, the tool, the workbench and the bake-off. Site factor is plumbed for site mode too.
- **D425:**
  - 4 big cases: 2 on plans at factor 4.0/6.0 (targets 6k/10k), 2 plan-free (areas 2,000/3,000, targets 12k/20k).
  - TSV columns site_factor/area/target; code-stated targets with a ±20% band and max_blocks = 1.25 × target.
  - 32k output and 40k echo for targets > 5,000, bake-off only.
  - Sandbox helpers are capped by placed cells; clear is uncapped.
  - `--only`.
  - 20k is reachable only plan-free on Clayton's plans.

---

## 8. Risks

**From the candidate designs, corrected (R1-R9):**
- **R1. Op-path change not needed.** `_Builder.box` already clamps to the bounds and caps only fills (shell.py:757-775). Only the sandbox helpers change.
- **R2. Cap of `task_max_usd` too small.** With a 2.0 cap the guard stops before the second repair, contrary to "the same 2 repair rounds". The default is 4.0, sized from the measured prompt sizes.
- **R3. Output limit keyed on max_blocks.** max_blocks defaults to 20,000, so a rule on it would move every existing case and every live request to 32k. It is keyed on the bake-off target instead.
- **R4. Plot formula.** Relief lies outside SITE, so it is not part of the side formula.
- **R5. Shell jobs hold the planner lock.** A multi-minute AI job would block the Designer and power jobs (§2.4).
- **R6. No-AI over-blocking.** Refusing on every reason except NO_KEY would stop Astra during an Anthropic outage. Only CHOICE/CREDITS apply to other providers.
- **R7. `Env` is frozen.** The late-set job starter goes through a holder object.
- **R8. Existing TSV rows.** They need no padding (DictReader gives None).
- **R9. New reply lane avoided.** A new `Reply.lane` value would touch voice, Discord and the dashboard union. Chat revise answers on INSTANT.

**Open risks:**
1. **Vendor limits unverified.** 32,000 output tokens has not been tried against the real vendor. A `bad_request` stops that model for the rest of the run (D378). Mitigations: the big cases are last, `--only` exists, and the runbook says to try one big case with `--limit 1` first.
2. **Big cases at the day cap.** The default `day_max_usd` (10) stops a big-case run partway (§4.6). The runbook says so.
3. **Chat outcome not reported.** The chat path's result arrives only in the workbench (job push). Voice and Discord users learn the outcome only by asking or opening the workbench.
4. **Two bills for a chat design.** It pays a Claude turn plus the designer. "Faster, cheaper" holds fully only for workbench and chat revisions.
5. **Bound-metered tries.** An unmeasured or maybe-billed try is metered at its bound, which can overstate the Costs view. This is deliberate: never $0.
6. **Plan-free plots up to 128 × 28 × 128.** The viewer recompiles per request (about 1-2 s at 20-40k cells) and the 3D view is slow at 20k blocks.
7. **Replay drift.** A free build re-checks its floor on replay. A change to `_door`'s ranking would refuse old free builds rather than silently moving their paste origin, as site mode does today.
8. **Shared engine regressions.** Moving `_rounds`/`_Capped` into shell_rounds risks breaking the bake-off; the existing 3,400-line suite is the guard.
9. **Windows.** No sandbox process change. The helper rule is pure Python inside the child. CI on Windows must still pass.
10. **Missed `site_mode` caller.** The 13 `site_mode`/`site_record` call sites must each be audited. A missed one would send a free record down a machine path (`_program` raises on missing program.json, so the failure is loud, not silent).

---

## 9. Open questions for Clayton

These go in the PR's Questions section. Everything else is decided above.

1. **Bigger answers for the live designer?** The live designer keeps the 16,000-token answer limit the bake-off measured. The big bake-off cases use 32,000. After the big-case run, should the live designer also use 32,000 for large requests? At Astra's price that is up to about $0.80 more per try, and the per-request cap would go to about $6.
   **Answered (Clayton, 2026-11-25, D429): yes,** 32,000 tokens for large requests with a cap of about $6 (`[shell] designer_big_max_usd`, 6.0); smaller requests keep 16,000 and $4.
2. **Day cap.** The live designer refuses once today's AI spend reaches `[budget] day_max_usd`, as the bake-off does. Every other lane only alerts at the day cap (D334). Keep the refusal, or only alert?
   **Answered (Clayton, 2026-11-25, D429): only alert,** as the other lanes; the per-request cap still holds.
