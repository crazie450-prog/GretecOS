# Iterating on a shell, then exporting it (WO-10.10)

The shell workbench is the separate window you asked for: the dashboard's **Build** view on monitor 2 (and the phone's **More** → Build). You design a shell, change it in words, compare tries side by side and go back to any of them, all without touching a world. A shell reaches a world only when you run `gos shell export` and paste the file in the Lab yourself ([`shells_lab.md`](shells_lab.md), D318). Decisions: D367-D370.

## What the workbench is

- **Words, theme and sizes.** The words box, the plan (and its power plan), the theme, the line type, **Max blocks** (1-200 000, default 20 000) and **Headroom** (blocks above the layout's top for the roof, 1-16, default 4). These are the sizes the gate supports; the layout's own clearances are the floor plan's and are not changed here (D370).
- **Design new**: a new design, GOS's procedural shell from the theme (free, no AI). **Revise**: a new version of the current design, made by GOS's own reviser from your words (free, no AI). Both run as jobs: the stage (laying out, checking the palette, writing the operations, the gate, saving), the time and a **Cancel** (a cancelled job saves nothing). One shell job at a time.
- **Ask the architect**: the same words to the deep lane's architect (AI, costs credit; `build_revise`) as a revision of the design picked in the list, whatever Chat worked on last and whatever the words are ("use brick instead of stone" too). With the AI off, GOS's own reviser does it. Its version shows in the list when it is done.
- **Design new** uses the theme, plans and sizes; GOS's free generator keeps the words only as the design's request. Words shape revisions and the architect.
- **On plans / Plan-free** (D424): Plan-free swaps the plan pickers for **Interior floor (blocks)**, a building with no machines sized by the roofed floor you ask for (25 to 6,000). The note under it is the plot GOS will use, worked out by the core ("plot about 29 × 29 at factor 2.0"), or why that area can't be done. **Advanced → Plot size factor** (1.25 to 6.0, default 2.0) makes the plot tighter or roomier; on plans it makes Design new a site design. A plan-free design is listed as "plan-free · ≥1,500 floor", and its viewer says "plan-free: no machines".
- **With a live shell designer** (D423, below): a status line under the buttons says which one and what it may spend ("Designer: astra · up to $4.00 a request · $7.60 left today"). **Design with the architect** has it draw a new design from the form (plans or a plan-free floor), and **Ask the architect** has it revise the design picked in the list from your words alone (clear the theme and sizes, or use Revise for those). Both run as shell jobs with a stage and Cancel. When it isn't ready, or the AI is off or at the day cap, both buttons are off and the line says why; GOS's own Design new and Revise still work. With none set the workbench is as before.
- **Versions**: every version of the current design, newest first: what was asked, by whom (GOS's own, the architect, or "AI (astra)" when the live shell designer drew it), its block count, its cost (free, or "AI (see Costs)": the architect's deep-lane task), and whether the gate repaired it. **Make current** puts any older version back (that is undo). Nothing is ever deleted.
- **Compare**: tick two versions: both are drawn side by side, with the materials (each `registry@meta`, the count in each and the change), the block counts and what changed in the operations.
- **The viewer**: the build guide's 3D drawing. Drag to turn and tilt, the wheel or buttons to zoom, the layer slider with that layer's materials, the whole shell's materials, and the floor plan's machines (solid outline) and keep-outs (dashed) inside the shell as ghosts, so you see the building around the line.

## Two kinds of shell: around the machines, or the building first (D422)

- **Layout-first** (the free **Design new**, `gos shell design`, and every shell saved before WO-10.16): GOS lays the machines out in rows first, then the shell is built around that floor plan. Its keep-outs and corridors are fixed before the building is drawn.
- **Site mode** (every new AI design: the architect's `build_request` and `build_revise`, and the bake-off): the building comes first. GOS gives the designer a square plot (the SITE) sized from the machines, and how much roofed floor they need (NEED: each machine's keep-out, the body plus a 1-block ring and 1 above, plus 25%, in roofed floor as well as in air). The designer builds anything it likes there: halls, wings, columns, inner walls. Then GOS fits every machine onto the ground floor (y 0) under the roof, around whatever it built, adds the walkways, the cable chase and the door, and checks it with the same gate. Where the machines go is still GOS's choice, never the model's.
- A site design that is too small, has no door to the outside, has no floor tall enough for the tallest machine or can't be packed is refused with the exact numbers (for example "the roofed floor holds 410 usable blocks ... the machines need 519 ... add at least 109"). The architect gets the reason and tries again.
- The notes and the result say how the machines fit: "8 machines fitted in the building (pass flow), interior 1289 of 519 needed (+25%), 148% spare, entrance auto".
- To try a site shell for free yourself: `& $gos shell design <plan-id> --theme th-foundry --mode site` (GOS's own building for the site).

## A building with no machines in it (plan-free, D424)

- When you want just the building and will put the machines inside yourself, ask for a **plan-free** building. It names no plans: you give the **interior floor area** instead, as blocks of roofed floor (25 to 6,000), for example "at least 1,500 blocks of roofed floor".
- GOS works out a square plot (the SITE) from the area: its side is `max(ceil(sqrt(2.0 x area)), ceil(sqrt(area)) + 4)`, at most 128, and it is 12 blocks high plus the headroom. 1,500 blocks of floor gives a 55 x 55 plot, and 6,000 gives 110 x 110. A **site factor** from 1.25 to 6.0 (default 2.0) makes the plot tighter or roomier. The same factor now also works for site-mode shells on plans (`--site-factor`).
- The check is site mode's floor and door rule, without the machines: at least that many columns of roofed floor, at least 3 high, all joined to one ground doorway 3 wide with a straight way out. There is no fit, no keep-outs and no program. A building that is too small is refused with the numbers, for example "the roofed floor joined to the west door has 1,212 columns at least 3 high; it needs 1,500: add at least 288".
- It exports as a `.schematic` like any shell (`gos shell export`), and you paste it in the Lab from its doorway. The viewer shows no machine ghosts. `gos shell refit` refuses a plan-free building: it has no machines to refit.
- Revising one keeps its area and plot. The area is a minimum, so a revision may add floor inside the plot. A different area is a new design.
- To try one for free: `& $gos shell design --mode free --area 1500 --theme th-marble-works` (GOS's own building: walls round the whole plot, a flat roof and a west door). The architect takes the same request in Chat ("a plan-free building with 1,500 blocks of floor"); without a number it asks you for one.

## The live shell designer (D423)

- With `[shell] designer = "astra"` in gos.toml (a `[[bakeoff.models]]` label, with its price; [setup_guide.md](../setup_guide.md) step 84), that model draws **every new AI shell design and revision**: the workbench's AI buttons, a revision you ask for in Chat (it starts a shell job at once: "Asked astra to revise ds-... (job ...)"), and the architect, which hands the request on (`ask_designer`) instead of writing the operations itself. It gets the bake-off's brief, no tools, the same gate and at most 2 repair rounds; a version it made shows **by architect**, its real cost and the designer's label. Unset (the default), nothing changes: the architect writes the design.
- **Spending.** One request may spend at most `[shell] designer_max_usd` (4.0 unless you change it, 0.5 to 20), every try and retry counted. Before each try GOS checks that the most it can cost still fits; if not it stops there: "stopped before try 3: $1.61 spent and the next try could cost up to $1.13, over this request's cap $2.50. Nothing was saved." Every answered try is in the Costs view under the lane `designer`; a try that broke off, or came without its token count, is counted at the most it can have cost (task `designer:...:bound`).
- **When it doesn't run**, it says so in one sentence and spends nothing: none set; not ready (no `[[bakeoff.models]]` entry with that label, no price for it, no key stored for its vendor, a `[bakeoff]` table GOS can't read); the AI turned off by you or paused for credits; or today's spend at the day cap ("today's AI spend $10.12 has reached the day cap $10.00 ... the shell designer is off until tomorrow, or raise day_max_usd"). Unlike the other AI work, which only warns at the day cap, the designer refuses there, as the bake-off does. Problems of the Anthropic API (no Anthropic key, an outage) stop only a Claude designer.
- **Its own errors** are plain sentences and never turn the rest of GOS's AI off: "astra refused the openai key: run `gos key set openai`", "openai says astra's quota or credits are used up", "astra didn't answer after 3 tries (rate limited)", "astra's 3 answers were all refused. Last: <the gate's reason>. $2.31 spent, nothing saved." GOS's own **Revise** (free) still works.
- While its model answers (a minute or more), the planner is free: Designer and power jobs go ahead. One shell job at a time still holds. **Cancel** takes effect before the next try; a try already sent finishes and is billed.
- From PowerShell (paid: it prints the cap and what one try can cost, and waits for `yes`):

```powershell
& $gos shell design <plan-id> --ai                    # a site design by the designer
& $gos shell design --mode free --area 1500 --ai      # a plan-free building by the designer
& $gos shell revise make the roof steeper --ai        # a new version of the current design
```

  Exit 0 saved, 1 refused or not run, 2 no designer ready or bad input.

## How to iterate

1. **Design.** Pick the plan and a theme (or leave it blank: the theme used nearby, else one that fits the line type), set Max blocks or Headroom if you like, press **Design new**. Or say it: "design a foundry shell around my steel line".
2. **Change it in words.** Type or say what to change: "make the roof steeper", "taller, and no windows on the north wall", "no lights", "fewer pillars", "more windows", "bigger windows", "a parapet", "no plinth". After a shell exists, Chat and voice revise the current shell instead of starting over (the router decides, in code, D369); "design another shell", "start over", or a request to make one ("design a shell around my EBF plan with no windows", "make a gothic theme with no lights") makes a new one, and words about something else ("add more lights to my base", "remove the walls in plan x") are not a revision. In Chat a change needs a part of the building to change ("the roof", "the windows", "the building taller"); in the workbench, Ask the architect takes any words.
   - **GOS's own reviser** (Revise, or any change while the AI is off) understands the roof (steeper, flatter, flat, a parapet), the height (taller, shorter, `by N`), windows, lights and pillars (none, more, fewer, on or off a side), the plinth and the eaves band. Anything else it says it can't do and writes nothing: ask the architect. A layout-first shell never goes lower than the machines' height; a site shell may, until the machines no longer fit (then the gate refuses it, naming the machine).
   - **A site shell's machines are fitted again** into each revised building. Machines whose places still fit stay where they were; only the ones the change displaced move.
   - **The architect** takes any words and changes only what you asked.
   - Either way the gate checks the result like any other design: a refused revision writes nothing and the current version stays.
3. **Undo.** Say "undo" (or "go back to the previous version"), or press **Make current** on any version.
4. **Compare** two versions, pick one, make it current.
5. **Export it**: `& $gos shell export <sh-id>` with the id of the version you chose (the viewer's title and `gos shell versions` show it), then paste it in the Lab ([`shells_lab.md`](shells_lab.md) D).
   - If the plan or its machines' footprints changed since the shell was designed, export refuses. A layout-first shell must be designed again. A site shell says whether its building still holds the new machines: if it does, `& $gos shell refit <sh-id>` makes a new version with the same building and the machines fitted again (free, no AI), and you export that one; if not, revise the building (make it bigger).

## Known limits

Some phrasings in Chat or voice neither design a shell nor revise the current one, because the router decides in code and needs a design verb or a part of the building that changes (D369): "I want a shell around X" and "add a lamp over the door" go to the fast lane, which can't write a shell; "put lights on the shell" goes to the deep lane as an ordinary task, not as a revision of the current design. For these, type the words in the workbench and press **Ask the architect**, which revises the design picked in the list whatever the words are (or **Design new** for a new shell). The dashboard's ask route (`POST /api/ask`, its `shell` field) lets any signed-in caller, a paired client device such as the phone included, name which design its words revise, as Ask the architect does (D370).

## The same from PowerShell

```powershell
& $gos shell versions                      # the designs; * is the current one
& $gos shell versions <ds-id>              # one design's versions; * is the current version
& $gos shell revise make the roof steeper  # a new version of the current design (GOS's reviser)
& $gos shell revise --headroom 8 --theme th-marble-works   # sizes or another theme's palette
& $gos shell undo                          # the current version's parent is current again
& $gos shell current <ds-id> <sh-id>       # any version current
& $gos shell compare <sh-id> <sh-id>       # materials and block counts side by side
& $gos shell refit <sh-id>                 # a site shell: today's machines fitted into the same building
& $gos shell design <plan-id> --theme th-foundry --mode site   # GOS's own site building (free)
& $gos shell design --mode free --area 1500 --theme th-marble-works   # a plan-free building (free)
```

`revise` and `refit` exit 0 saved, 1 refused (nothing written), 2 bad input. `refit` works on the current version of its design only.

## The revise eval

`& $gos eval shell-revise <plan-id> --fake` runs six briefs with a follow-up each (`tests/evals/shell_revise.tsv`) with GOS's own reviser: free, and the same cases run in CI. Without `--fake` the architect answers each follow-up (paid: about $0.50-$1.00 a case, $3.00-$6.00 for the six, never more than `[budget] task_max_usd` a case). Each case is scored by the gate, by whether the follow-up changed what was asked, and by whether the rest of the shell stayed as it was. Both run on a copy of your DB, vault and that plan in `data\eval\shell-revise-home`, so your own shells don't change.

## Where things are

- Builds (versions): `GOS_HOME\data\build\shells\<sh-id>\` (written once, never changed). A site shell's folder also has `program.json` (the machines it was fitted with) and, when it kept an earlier version's places, `prior.json` (that version's floor plan).
- Designs (the chains): `GOS_HOME\data\build\designs\<ds-id>.json`, and `current.json` (the design you work on).
- A shell made before WO-10.10 shows as a one-version design and gets its file the first time you revise it or make a version current.
