# Setup wizard (WO-2.9)

The first-run setup wizard configures GOS from the dashboard alone (plan 7.14 rule 8, D167-D169).

## What it does

1. **Paths:** Prism folder, instances folder, Play / Export / Lab instance names, GOS_APP, GOS_HOME. Checked with `gos doctor`'s checks before saving.
2. **Anthropic API key:** stored in Windows Credential Manager; **Test** makes one tiny fast-lane call (metered as `setup-test`).
3. **Discord bot token** (optional until WO-9.1): Credential Manager.
4. **Headset:** microphone (`[voice] input_device`) and the output for spoken answers (`[voice] output_endpoint`, the Arctis Nova Elite's Chat endpoint, D56).
5. **Talk key** and mode (QA:V8, D180).
6. **Wake phrase** (optional, PM08; WO-5.4 has the last word).
7. **Checklist:** doctor's checks with Re-run; **Finish setup** writes `[setup] completed_utc` to gos.toml.

Saved settings take effect at GOS's next start (log in again, or `gos stop` then `gos serve`). Secrets work at once. Re-open the wizard any time: Settings > Setup wizard (`#/setup`).

## Clayton's PC steps (deployment test)

```powershell
$ProgressPreference = 'SilentlyContinue'
cd <your own clone>
git pull
uv run gos deploy                # installs into GOS_APP and builds the dashboard
```

Then open the dashboard on monitor 2 (`http://gos.localhost:8765/`), pair it if asked (`gos pair` shows the code), and walk the wizard:

- Paths: every row green for Play, Export and Lab.
- Store the Anthropic key and press **Test**: "the API answered on claude-haiku-5-5".
- Headset: pick the Arctis Nova Elite **Chat** endpoint for spoken answers; check the microphone.
- Talk key: keep F13 (or your mapped key); run `uv run gos voice keys play` to list GTNH's bindings on it, and check Discord's Keybinds page by hand.
- Finish, then `gos stop` and `gos serve` so the core reads the new gos.toml.
- Compare the wizard with the approved "First-run setup wizard" board (D92) and note differences.

## Tests

- `uv run pytest tests/unit/setup` (gos.toml edits, steps, secrets, routes; fake keyring and fake Prism tree).
- `pnpm -C dashboard test` (`tests/setup.test.tsx`).
- `pnpm -C dashboard e2e` (`e2e/setup.spec.ts`: an empty GOS_HOME configured by the wizard alone, against `serve_core.py --fresh`).
