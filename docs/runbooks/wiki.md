# Wiki cache (WO-8.3, plan 7.8, D290-D292, V14)

GOS keeps a **personal** cache of the GTNH wiki in `%GOS_HOME%\data\gos.sqlite` so the deep lane's researcher can read and cite it (`w:<Page>@<revid>`). Nothing is cached until you have seen and recorded the wiki's licence. The cache is never committed, bundled or shared.

How GOS talks to the wiki (all in code, `gregtec.wiki.api`): one request at a time across every GOS process (the core and any `gos wiki` command share `data\wiki.lock`), 1 s apart, `maxlag=5`, a descriptive User-Agent (`GregtecOS-wiki-cache/0.1 (...; +https://github.com/crazie450-prog/GretecOS)`, no name or e-mail), gzip, at most 50 titles a request, read-only `action=query` GETs, back-off on lag, 429 and 5xx. If the wiki puts an anti-bot gate in the way (a 403, a challenge or login page), GOS **stops** and records it; it never tries another way in.

## A. Licence check (V14) — Clayton's PC

In your own normal window, after this PR is merged and deployed (`uv run gos deploy`). **`gos` in your own window** is GOS_APP's copy: paste `$gos = "C:\GregtecOS-app\current\.venv\Scripts\gos.exe"` once, then type `& $gos ...` wherever this page writes `gos ...` for you (nothing puts `gos.exe` on your PATH, and `uv run gos` in your clone would run the clone's code, not the deployed one).

```powershell
$ProgressPreference = 'SilentlyContinue'
& $gos wiki licence
```

It runs `api.php?action=query&meta=siteinfo&siprop=general|rightsinfo&format=json` and prints the wiki's name, licence and terms URL, then asks `Record it and allow the cache? [y/N]`.

- Read the licence. If you're happy with a personal, never-shared cache under it, answer `y`. Note the licence text in the PR or tell the PM, and V14 is done.
- If it says **the wiki states no licence**: don't cache; ask the GTNH wiki admins (GTNH Discord) what applies.
- If it says **Stopped: ... refused scripted access**: the wiki has an anti-bot gate. Don't work around it. Ask the wiki admins (GTNH Discord) for API access for a personal assistant cache or for a `Special:Export` XML dump, and tell the PM which you got. Run `gos wiki licence` again once they allow it; a successful run clears the block.

## B. First crawl — Clayton's PC

```powershell
& $gos wiki crawl
```

One request a second, 50 pages each: a wiki of a few thousand pages takes a few minutes; it prints progress after each batch. Ctrl+C stops it after the current batch; run it again to go on. `gos wiki crawl --again` starts over (needed only if the daily refresh hasn't run for over 30 days, which `gos wiki status` and the maintenance log say).

Check:

```powershell
& $gos wiki status                    # licence, pages, crawl finished, refresh point
& $gos wiki show "Electric Blast Furnace"   # its w: id, the attribution line, the first lines
```

## C. Daily refresh

Nothing to do: idle maintenance (game closed, 30 idle minutes, once a day) runs the `wiki` job, which asks the wiki for the pages changed since the last refresh. `gos vault maintain --job wiki` runs it now; `gos wiki refresh` does the same without the other jobs. Running these (or a crawl) while the core is up is fine: requests still go one at a time, and a researcher's fetch meanwhile may say the wiki is busy and to try again in a minute. The job is skipped with no licence, before the crawl ends, or after a gate (the health strip's maintenance line shows why).

## D. In use

Ask the deep lane a wiki question ("what does the wiki say about the pyrolyse oven?"). The researcher subagent, the only agent with the wiki tools, reads the pages; the answer cites `w:` ids and GOS adds the wiki's attribution line (site, licence) at its end. Text on a wiki page is data: an instruction written in a page is ignored (and the researcher can't act on one: its tools are read-only).

## E. The wiki eval (plan 9's 15 cases; WO-8.4, D363) — Clayton's PC, after B

A case cites pages of **your** crawl by their `w:<Title>@<revid>` ids, so the 15 cases are written on your PC from
your cache, never made up in the cloud. They live in `$gh\data\evals\wiki_questions.tsv` (GOS never commits it).

1. **Write the cases** (free): name pages you know, a few at a time:

   ```powershell
   & $gos eval wiki --make-cases --page "Electric Blast Furnace" --page "Pyrolyse Oven" --page "Steam Turbine"
   ```

   For each page it shows the `w:` id and the first lines, proposes a question (Enter keeps it, or type your own), and
   asks for the kind (`fact`, or `injected` for a page that holds an instruction or a claim the answer must not
   repeat), the **facts** a right answer must contain (`;` between; words, no numbers), the phrases it must **not**
   say, and a yes. A tab or a `"` can't go in a case (it is refused with a reason); an `injected` case needs at least
   one phrase it must not say; a fact not in the page's text as typed is flagged (`Check: ...`): fix the typo. Each
   kept case is saved at once (`w01`, `w02`, ...). Run it again until the file has **15** cases;
   mix machines, mechanics and items, and include at least one `injected` page if the wiki has one.
2. **Check the plumbing** (free): `& $gos eval wiki --fake`. It answers with the wiki tools and no model. A row that
   fails here usually means a fact isn't in the page's opening sentence (the oracle only reads that far): fine for the
   paid run, but check the fact is really on the page.
3. **The paid run** (deep lane: 15 tasks, each capped at `[budget] task_max_usd`: **$30 worst case** at the default $2,
   printed first): `& $gos eval wiki | Tee-Object -FilePath "$gh\data\reports\wiki-eval.txt"`, type `yes` after the
   `worst case $` line (through `Tee-Object` the prompt may not show until you press Enter). It refuses while the AI is
   paused or off, or when today's spend plus the worst case would pass `[budget] day_max_usd`: then raise it for the
   sitting (checklist 3.5, first paragraph) or run it another day. A `capped` row stopped at the task cap: re-run it; it says nothing
   about the answer. Pass: `passed: 15 of 15
   (plan 9 asks for 15)`, `Result: PASS`. Paste: `wiki-eval.txt`.

A case whose page changed on the wiki since you confirmed it (the daily refresh fetched a new revision) shows
`not checked: ... changed since it was confirmed`: run `--make-cases` for that page again and keep the new case (then
delete the old row from the file).
