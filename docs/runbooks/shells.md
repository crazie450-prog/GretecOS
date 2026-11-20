# Iterating on a shell, then exporting it (WO-10.10)

The shell workbench is the separate window you asked for: the dashboard's **Build** view on monitor 2 (and the phone's **More** → Build). You design a shell, change it in words, compare tries side by side and go back to any of them, all without touching a world. A shell reaches a world only when you run `gos shell export` and paste the file in the Lab yourself ([`shells_lab.md`](shells_lab.md), D318). Decisions: D367-D370.

## What the workbench is

- **Words, theme and sizes.** The words box, the plan (and its power plan), the theme, the line type, **Max blocks** (1-200 000, default 20 000) and **Headroom** (blocks above the layout's top for the roof, 1-16, default 4). These are the sizes the gate supports; the layout's own clearances are the floor plan's and are not changed here (D370).
- **Design new**: a new design, GOS's procedural shell from the theme (free, no AI). **Revise**: a new version of the current design, made by GOS's own reviser from your words (free, no AI). Both run as jobs: the stage (laying out, checking the palette, writing the operations, the gate, saving), the time and a **Cancel** (a cancelled job saves nothing). One shell job at a time.
- **Ask the architect**: the same words to the deep lane's architect (AI, costs credit; `build_revise`) as a revision of the design picked in the list, whatever Chat worked on last and whatever the words are ("use brick instead of stone" too). With the AI off, GOS's own reviser does it. Its version shows in the list when it is done.
- **Design new** uses the theme, plans and sizes; GOS's free generator keeps the words only as the design's request. Words shape revisions and the architect.
- **Versions**: every version of the current design, newest first: what was asked, by whom (GOS's own or the architect), its block count, its cost (free, or "AI (see Costs)": the architect's deep-lane task), and whether the gate repaired it. **Make current** puts any older version back (that is undo). Nothing is ever deleted.
- **Compare**: tick two versions: both are drawn side by side, with the materials (each `registry@meta`, the count in each and the change), the block counts and what changed in the operations.
- **The viewer**: the build guide's 3D drawing. Drag to turn and tilt, the wheel or buttons to zoom, the layer slider with that layer's materials, the whole shell's materials, and the floor plan's machines (solid outline) and keep-outs (dashed) inside the shell as ghosts, so you see the building around the line.

## How to iterate

1. **Design.** Pick the plan and a theme (or leave it blank: the theme used nearby, else one that fits the line type), set Max blocks or Headroom if you like, press **Design new**. Or say it: "design a foundry shell around my steel line".
2. **Change it in words.** Type or say what to change: "make the roof steeper", "taller, and no windows on the north wall", "no lights", "fewer pillars", "more windows", "bigger windows", "a parapet", "no plinth". After a shell exists, Chat and voice revise the current shell instead of starting over (the router decides, in code, D369); "design another shell", "start over", or a request to make one ("design a shell around my EBF plan with no windows", "make a gothic theme with no lights") makes a new one, and words about something else ("add more lights to my base", "remove the walls in plan x") are not a revision. In Chat a change needs a part of the building to change ("the roof", "the windows", "the building taller"); in the workbench, Ask the architect takes any words.
   - **GOS's own reviser** (Revise, or any change while the AI is off) understands the roof (steeper, flatter, flat, a parapet), the height (taller, shorter, `by N`), windows, lights and pillars (none, more, fewer, on or off a side), the plinth and the eaves band. Anything else it says it can't do and writes nothing: ask the architect.
   - **The architect** takes any words and changes only what you asked.
   - Either way the gate checks the result like any other design: a refused revision writes nothing and the current version stays.
3. **Undo.** Say "undo" (or "go back to the previous version"), or press **Make current** on any version.
4. **Compare** two versions, pick one, make it current.
5. **Export it**: `& $gos shell export <sh-id>` with the id of the version you chose (the viewer's title and `gos shell versions` show it), then paste it in the Lab ([`shells_lab.md`](shells_lab.md) D).

## Known limits

Some phrasings in Chat or voice are not taken as a revision of the current shell, because the router needs a part of the building to change (D369): "I want a shell around X" goes to the fast lane, which neither designs nor revises a shell; "add a lamp over the door" and "put lights on the shell" are not taken as changes to the shell either. For these, type the words in the workbench and press **Ask the architect**, which revises the design picked in the list whatever the words are (or **Design new** for a new shell). The dashboard's ask route (`POST /api/ask`, its `shell` field) lets any signed-in caller, a paired client device such as the phone included, name which design its words revise, as Ask the architect does.

## The same from PowerShell

```powershell
& $gos shell versions                      # the designs; * is the current one
& $gos shell versions <ds-id>              # one design's versions; * is the current version
& $gos shell revise make the roof steeper  # a new version of the current design (GOS's reviser)
& $gos shell revise --headroom 8 --theme th-marble-works   # sizes or another theme's palette
& $gos shell undo                          # the current version's parent is current again
& $gos shell current <ds-id> <sh-id>       # any version current
& $gos shell compare <sh-id> <sh-id>       # materials and block counts side by side
```

`revise` exits 0 saved, 1 refused (nothing written), 2 bad input.

## The revise eval

`& $gos eval shell-revise <plan-id> --fake` runs six briefs with a follow-up each (`tests/evals/shell_revise.tsv`) with GOS's own reviser: free, and the same cases run in CI. Without `--fake` the architect answers each follow-up (paid: about $0.50-$1.00 a case, $3.00-$6.00 for the six, never more than `[budget] task_max_usd` a case). Each case is scored by the gate, by whether the follow-up changed what was asked, and by whether the rest of the shell stayed as it was. Both run on a copy of your DB, vault and that plan in `data\eval\shell-revise-home`, so your own shells don't change.

## Where things are

- Builds (versions): `GOS_HOME\data\build\shells\<sh-id>\` (written once, never changed).
- Designs (the chains): `GOS_HOME\data\build\designs\<ds-id>.json`, and `current.json` (the design you work on).
- A shell made before WO-10.10 shows as a one-version design and gets its file the first time you revise it or make a version current.
