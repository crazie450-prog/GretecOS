# The event store, sessions and the journal (WO-6.3): the Lab session check

What WO-6.3 adds (D263–D265) and the check Clayton runs on the PC (plan §8 row 6.3: "a scripted
Lab session gives >= 95% event recall"). Claude Code built and measured it on a fixture: the real
watchers over a fake GTNH-Lab instance's files and GregtecLink listener lines, two scripted
sessions, 28 ground-truth events, recall 100% (`tests/unit/watchers/test_session_recall.py`).
Everything below is **GTNH-Lab only**; Play is not touched by this WO.

```powershell
$gos  = "C:\GregtecOS-app\current\.venv\Scripts\gos.exe"
$lab  = "C:\Games\PrismLauncher\instances\GTNH-Lab\.minecraft"
$ProgressPreference = 'SilentlyContinue'
```

## How it works

- **The event store.** Every well-formed event the watchers post (`POST /api/game/events`) goes
  into the core DB's `event` table, keyed by the watchers' own event id, so a batch sent again
  adds nothing. `machine_census` is My base's state, not an event, and is not kept. A failed store
  answers 503 and the watchers send the batch again (D263).
- **Sessions** per instance: one starts at the **login line**, and ends at the **quit line**
  (`Stopping server`, a new `world_closed` event), a **crash** report, or after **15 minutes**
  with no activity (it then ends at its last activity). Activity after an idle end starts a new
  session (`resume`). A crash or quit's own save files, read up to 2 minutes later, still belong
  to it (D264).
- **The journal** (`vault\log\YYYY-MM-DD.md`, Play's sessions only; Lab's stay in the DB and the
  Activity view): the core writes a block every hour of a session and one at its end, at most 40
  lines a session, through `gregtec.vault` (never `me/`). Counts (`n_quest: 2`, `n_built: 3`...)
  are computed by code; the closing `note:` lines (one or two, Haiku, off in no-AI mode) may use
  only those numbers. A block in another day's log opens with `session: s<id> ..., continued`
  (D263).
- **The finished age's recap** (D162, D265): when the tier the dashboard shows rises (a quest, or
  your override in My base), the core closes the age and keeps its recap: hours played (Play's
  sessions inside the age), machines built (the listener's `machine_built` events) and the best
  net power OC measured (`db:tel_sample/...`, or "no readings" before OC). An open dashboard shows
  it with the new theme; the Activity view keeps every recap; the journal gets `age_*` lines.
- **Agents** read it with `events_recent(window, kinds)` and `session_summary(id)` (counts by
  GOS), through the `session-scribe` skill.

## 1. Before you start

- WO-6.2's listener is on in Lab (`docs/runbooks/machine_events.md` §1).
- GOS is running (`& $gos serve` or the logon task) with this build deployed.

## 2. The scripted Lab session (the §8 acceptance)

Write down the time of each step as you do it; that list is the ground truth.

1. `& $gos launch lab`, open a survival test world (e.g. `lab-progress`). The watchers start with
   Lab (D259). The login line starts a session.
2. Say something in chat (`hello gos`).
3. Place 3 GT machines (one a multiblock controller), break 1.
4. Complete one quest (hand in an item for an easy one, or `/bq_admin complete <id>`).
5. Walk at least 50 blocks, pick up some items.
6. Prospect or walk over an ore vein until VisualProspecting (or the chat line) reports it.
7. Die once (`/kill`).
8. Go to the Nether and back.
9. Place a few ordinary blocks.
10. Press Esc for 10 seconds, then go on.
11. Quit to the title screen (the quit line ends the session), then close the game.

Then compare:

```powershell
& $gos tool session_summary
& $gos tool events_recent '{\"window\": \"session\"}'
```

and the dashboard's **Activity** view (nav, under Base). Recall = the steps above (count each
machine, quest, death, vein, dimension change, chat line and pause as one event) that show as an
event in that session, divided by all of them. **Pass: >= 95%**, the session starts at the login
and ends `quit`, and the death counts once.

Optional: open the world again, do nothing for 16 minutes: the session ends `idle` at your last
action, and the next movement starts a new one (`resume`).

Record the result (recall, misses) in the PR.

## 3. The tier-up recap

In the dashboard's **Base** view set the tier override one stage up (e.g. Steam to LV) while the
dashboard is open on monitor 2: the theme fades over, and a panel shows the finished age's hours
played, machines built and best net power (no readings until OC reports power). Set the override
back (Clear): nothing changes (only a rise closes an age). The Activity view lists the recap under
**Finished ages**; today's log in the vault has the `age_*` lines. This changes only GOS's DB and
vault, never a game file.
