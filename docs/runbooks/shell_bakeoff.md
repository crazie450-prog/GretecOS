# The shell designer bake-off (WO-10.11)

You asked for it on 2026-11-20: "I do think a bakeoff so to speak would be a good idea so i can also test out the framework of the building generator app". A bake-off gives the same building requests to several AI models, checks every answer with GOS's own gate, and lets you pick the better building without knowing which model drew it. Decisions: D374-D377.

There are two walk-throughs here. **Part 1 is free** (fake models, no key, nothing leaves the PC) and doubles as your hands-on tour of the building tool. **Part 2 is paid**: real models from Anthropic and OpenAI, your money, your yes.

**What never happens.** Nothing here touches a world. A model gets words and numbers and gives back a list of building steps; it gets no tools, runs no code and reads no file. GOS turns the steps into blocks and checks them (only blocks from the theme's palette, nothing with an inventory or a screen, nothing where the machines go, not more than the block limit). A bake-off building is kept apart from your own shells: it is never your current design, `gos shell list` doesn't show it and `gos shell export` can't export it.

Use your own PowerShell window with the block from the deployment checklist's "Windows used throughout" pasted in (`$gos`, `$gh`, `$lab`). The core must be running for the dashboard (`& $gos serve`, or it is already running since login).

## Part 1. The free tour (about 30 minutes, costs nothing)

### A. Look at the building tool first

1. Find a saved plan to build around. Run:
   ```powershell
   Get-ChildItem "$gh\data\plans\*.json" | Select-Object -ExpandProperty BaseName
   ```
   Each line is a saved plan's id. Pick the production line you want a building around. If nothing is listed, make a plan in the dashboard's **Designer** first (checklist 3.6.2). Below, `<plan-id>` means that id. (The Shell workbench's **Plan** list in step 3 shows the same plans with their names.)
2. Open the dashboard on monitor 2 (`http://gos.localhost:8765/`) and click **Build** in the left list.
3. Scroll to **Shell workbench**. In **Plan** pick your plan. In **Theme** pick **Foundry**. In the words box type:
   ```
   a heavy foundry around the line
   ```
   Click **Design new**. A progress line shows the stages; a few seconds later the building is drawn below.
4. Type in the words box, then click **Revise**:
   ```
   make the roof steeper
   ```
   The **Versions** list now has v1 and v2. Do it once more with:
   ```
   taller, and no windows on the north wall
   ```
5. Undo: in the Versions list click **Make current** on v2. Nothing is deleted; v3 is still in the list.
6. Compare: tick **compare** on two versions. Both are drawn side by side with a table of the blocks each uses.
7. The 3D viewer: drag to turn it, use the wheel or **+** and **−** to zoom, and drag the **Layer** slider down to see the building one layer at a time. The dashed outlines inside are your machines and the space around them.

That is the tool. These buttons use GOS's own free generator; **Ask the architect** sends the same words to the AI (paid, a few cents to a dollar a go; not needed for this tour). More: [`shells.md`](shells.md).

### B. A bake-off with the fake models

The fake models are small programs inside GOS that answer like a model would. `fake-plain` draws a simple hall, `fake-ornate` a fancier one, and `fake-sloppy` gets its first answer wrong on purpose so you can see the gate refuse it and the repair round fix it.

8. See which models you can name:
   ```powershell
   & $gos shell bakeoff models
   ```
9. Run it (a minute or two; it prints "Contacts: nothing" and asks nothing, because it is free):
   ```powershell
   & $gos shell bakeoff run <plan-id> --models fake-plain,fake-ornate,fake-sloppy
   ```
   If you also have a power plan for that line, add `--large <plan-id>,<power-plan-id>` so three of the six requests get a bigger building.
   The last lines name the run (it looks like `bo-20261121-193000-ab12`). Below, `<run>` means that name.
10. In the dashboard's **Build** view scroll down to **Shell bake-off**. You see the first request, what was asked next ("Then"), and the answers side by side as **A**, **B**, **C**. Which model is which letter is hidden, and it is shuffled for every request.
11. Click **After the follow-up** to see each building after the change that was asked; click **The first design** to go back. Turn the buildings and use the layer sliders as in step 7. The table under them compares the blocks of the first two.
12. Pick: **A is better**, **B is better** (and so on for each letter), **Tie**, or **None is good**. Then click the next request's name at the top and pick again, until it says "every case is rated".
13. Click **Name the models and show the report**. The table now says which model was which, how often its answer passed the gate on the first try, how many repair rounds it needed, the blocks, the cost (free here) and your picks. After this the picks can't be changed for this run.
14. The same report as a file: `%USERPROFILE%\GregtecOS\data\reports\shell-bakeoff-<run>.md`.

Prefer the keyboard? `& $gos shell bakeoff show <run>` shows a run, `& $gos shell bakeoff rate <run>` asks for each pick in turn, `& $gos shell bakeoff report <run>` prints the report.

### C. From a design to the Lab (your own shell, not a bake-off one)

A bake-off building can't be exported. To put a building into the Lab, use one of your own from step 3-5:

15. In the Shell workbench, make the version you like current. Its id is in the viewer's title (it starts with `sh-`).
16. Write the file:
    ```powershell
    & $gos shell export <sh-id>
    ```
    It prints where the `.schematic` file is and where to stand.
17. Copy it into the Lab:
    ```powershell
    Copy-Item "$gh\data\schematics\shells\<sh-id>.schematic" "$lab\config\worldedit\schematics\"
    ```
18. In the **Lab** world (never Play), stand where step 16 said, then type `//schem load <sh-id>` and `//paste`. Never `//rotate` it. The full checks are in [`shells_lab.md`](shells_lab.md) D.
19. Or look at it as a ghost first with Schematica: copy the same file into the Lab's `schematics\` folder, open Schematica's menu, load it, and move it to the spot from step 16 with the printer off. (Untested with Schematica-GTNH; tell us what you see.)

### D. Clean up

20. Remove the practice run (it says what it removes and waits for `yes`):
    ```powershell
    & $gos shell bakeoff delete <run>
    ```

## Part 2. The paid bake-off (real models)

### E. What it costs

One run asks each model for 6 buildings and 6 changes: 12 answers, plus up to 2 repair rounds each when the gate refuses an answer. Each answer may use at most 16,000 output tokens, thinking included.

With the prices read on 2026-11-20, a run of all 6 requests is likely **about $1 for Claude Opus 5.5 and about $2.50 to $3 for GPT-6 Astra**, if every answer passes at once and uses a quarter of that limit. If every answer used the whole limit and every repair round, it would be about $14 and $35; it can't get there, because the run stops at its cap, which is **$10 for the whole run unless you change it**. The command prints its own estimate for your models before it asks; trust that one, not this page. Prices move: check the pricing pages below on the day.

### F. Store the two keys (once)

21. Anthropic: you already stored it in the setup wizard. Check:
    ```powershell
    & $gos key status
    ```
22. OpenAI: make a key at `https://platform.openai.com/` (API keys), add a little credit there, then:
    ```powershell
    & $gos key set openai
    ```
    Paste the key when asked (it stays hidden) and press Enter. It goes into Windows Credential Manager, never into a file. **Never paste a key into gos.toml, a chat or a PR.**

### G. Name the models in gos.toml

23. Open `%USERPROFILE%\GregtecOS\config\gos.toml` in Notepad and add this at the end:
    ```toml
    [bakeoff]
    cap_usd = 10.0             # the most one run may spend

    [[bakeoff.models]]
    label = "opus"             # your short name for it
    provider = "anthropic"
    model = "claude-opus-5-5"  # GOS knows this one's price already

    [[bakeoff.models]]
    label = "astra"
    provider = "openai"
    model = "gpt-6-astra"
    effort = "high"            # optional: how hard it thinks
    input_usd = 10.00          # price per million tokens, from OpenAI's pricing page
    output_usd = 50.00
    cached_input_usd = 1.00
    ```
    - **The model id and the prices are yours to check on the day.** OpenAI: the id is on `https://developers.openai.com/api/docs/models` ("Model ID"), the prices on `https://developers.openai.com/api/docs/pricing` (Standard, "Input", "Output", "Cached input", per 1M tokens). Anthropic: `https://platform.claude.com/docs/en/about-claude/pricing`. On 2026-11-20 and again on 2026-11-21 OpenAI's pages listed `gpt-6-astra` at $10 in / $50 out / $1 cached and `gpt-5.6-sol` at $4 / $20 / $0.40 (a promotional price until 2026-11-30). The same page listed newer models too, for one `gpt-6.2-astra` at $8 / $40 / $0.40: the example uses `gpt-6-astra` only because that is the model the claim you were sent was about. Compare whichever you like; a second `[[bakeoff.models]]` block with another `label` adds one.
    - Leave the three price lines out and GOS shows that model's cost as **unknown** and limits it by tokens instead of dollars (`cap_tokens`, 1,000,000 by default). GOS never guesses a price. The run then asks "Type yes to spend an unknown amount" (or "up to $... plus an unknown amount for" that model) instead of one dollar figure, and that model's money is **not counted in your day limit or the credit alerts**, because GOS can't count money it has no price for: the token limit is all that holds it, and the vendor's bill tells you what it cost. Putting the prices in is the safer choice.
    - `effort` is optional (`low`, `medium`, `high`, `xhigh`, `max`). Left out, each model uses its own default, which is not the same for every model; set it the same for both if you want that held equal.
24. Check that GOS reads it and sees both keys:
    ```powershell
    & $gos shell bakeoff models
    ```
    Each of your two models must say `stored` at the end of its line.

### H. Run it

25. Start with one request to see it work (likely well under $1 for both models together):
    ```powershell
    & $gos shell bakeoff run <plan-id> --models opus,astra --limit 1
    ```
    Read what it prints: how many answers it will ask for, each model's likely and worst-case cost, "Contacts: api.anthropic.com, api.openai.com", and the hard cap. Type `yes` and press Enter to spend it; anything else stops with nothing spent.
26. Then the full six, the same day or later (add `--large <plan-id>,<power-plan-id>` if you have a power plan, and `--cap-usd 6` if you want a lower limit for this run):
    ```powershell
    & $gos shell bakeoff run <plan-id> --models opus,astra
    ```
    It takes a few minutes per model. **Ctrl+C** stops it cleanly: what is finished stays, nothing is left half-written. If it reaches the cap it stops the same way and says so.
    If you already spent something today (step 25 did), it may print "Today's limit ([budget] day_max_usd) leaves $9.55: this run's cap is lowered from $10.00 to that". That is fine: the run can never take the day over your day limit, and the six requests likely cost far less. Type `yes` as before.
    If the connection drops in the middle of an answer, GOS tries that answer again (at most twice). The vendor may still charge for the broken one, and GOS can't see how much, so it counts each at the most it could have cost against the cap and tells you at the end ("... call(s) broke off after they were sent"). Your bill on the vendor's site is the last word.
27. If it says **Not run**, the line says why: a key is missing (it names the `gos key set` command), the AI is switched off or paused for credits (No-AI mode; the fake models still run), or today's spend has already reached your day limit (`[budget] day_max_usd` in `gos.toml`, $10 unless you changed it): run it another day, or raise that number, save, and run the command again.

### I. Rate it blind, then read the report

28. Rate as in steps 10-12. Take your time; look at both the first design and the one after the follow-up. Do not look at `run.json` in the run's folder before you have rated: that file is the only place that knows which letter is which.
29. Click **Name the models and show the report** (or `& $gos shell bakeoff report <run>`).

### J. What the numbers mean, and what they don't

- **picked**: how often you chose that model's building. This is the only number about looks.
- **first try / passed**: how many of its answers the gate accepted at once, and in the end. **repair rounds**: how many times, on average, GOS had to send an answer back. A model that needs many is slower and dearer, and may fail on a bigger building.
- **asked / kept**: for the follow-up, whether it changed what was asked and left the rest alone (checked by code, for simple requests only).
- **cost / seconds / tokens**: what the answers took. A cost of "unknown" means you gave no price. If the report has a line about calls that "broke off", the vendor may have charged a little more than the cost column says (that line gives the most it can be).
- **Six requests is a first look, not proof.** If one model is ahead 4 to 2, that can be chance; 6 to 0 with clearly better buildings is worth believing. To be surer, run it again another day (each run shuffles the letters anew) or with other plans, and see whether the same model wins.
- The bake-off only compares. Which model draws your real shells does not change: that stays the architect in `[models]` (Anthropic) until you decide otherwise (checklist 5.11.17).

Your usual cost page counts these runs too (**Costs** view, lane `bakeoff`).

## If something is off

- `no bake-off model 'x'`: the label isn't in `gos.toml`; `& $gos shell bakeoff models` lists what is.
- `OpenAI refused the key (HTTP 401)`: store it again (step 22). `out of credit or at a spend limit`: add credit on OpenAI's site or raise its limit there.
- `OpenAI refused the request (HTTP 400 or 404)` with a note about the model: the id in `gos.toml` is wrong, or your account can't use that model; check OpenAI's models page.
- A model shows "no design" for every request: its answers never passed the gate. The report's last column says why (for example a block that isn't in the palette, or too many blocks).
- A run still says `running` although nothing is running (the window was closed or the PC restarted): it can't be rated; delete it (`& $gos shell bakeoff delete <run>`) and run again.
- `case ... can't be set up`: the plan isn't saved, or the theme isn't in your vault (`& $gos theme list`).

## Where things are

- A run: `%USERPROFILE%\GregtecOS\data\bakeoff\<run>\` (its buildings and `run.json`).
- Reports: `%USERPROFILE%\GregtecOS\data\reports\shell-bakeoff-<run>.md`.
- The six requests: `tests\evals\shell_bakeoff.tsv` in the app folder (a small table; `--cases <file>` runs your own).
