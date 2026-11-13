# Wiki cache (WO-8.3, plan 7.8, D290-D292, V14)

GOS keeps a **personal** cache of the GTNH wiki in `%GOS_HOME%\data\gos.sqlite` so the deep lane's researcher can read and cite it (`w:<Page>@<revid>`). Nothing is cached until you have seen and recorded the wiki's licence. The cache is never committed, bundled or shared.

How GOS talks to the wiki (all in code, `gregtec.wiki.api`): one request at a time across every GOS process (the core and any `gos wiki` command share `data\wiki.lock`), 1 s apart, `maxlag=5`, a descriptive User-Agent (`GregtecOS-wiki-cache/0.1 (...; +https://github.com/crazie450-prog/GretecOS)`, no name or e-mail), gzip, at most 50 titles a request, read-only `action=query` GETs, back-off on lag, 429 and 5xx. If the wiki puts an anti-bot gate in the way (a 403, a challenge or login page), GOS **stops** and records it; it never tries another way in.

## A. Licence check (V14) — Clayton's PC

From your own clone, after this PR is merged and deployed (`uv run gos deploy`):

```powershell
$ProgressPreference = 'SilentlyContinue'
gos wiki licence
```

It runs `api.php?action=query&meta=siteinfo&siprop=general|rightsinfo&format=json` and prints the wiki's name, licence and terms URL, then asks `Record it and allow the cache? [y/N]`.

- Read the licence. If you're happy with a personal, never-shared cache under it, answer `y`. Note the licence text in the PR or tell the PM, and V14 is done.
- If it says **the wiki states no licence**: don't cache; ask the GTNH wiki admins (GTNH Discord) what applies.
- If it says **Stopped: ... refused scripted access**: the wiki has an anti-bot gate. Don't work around it. Ask the wiki admins (GTNH Discord) for API access for a personal assistant cache or for a `Special:Export` XML dump, and tell the PM which you got. Run `gos wiki licence` again once they allow it; a successful run clears the block.

## B. First crawl — Clayton's PC

```powershell
gos wiki crawl
```

One request a second, 50 pages each: a wiki of a few thousand pages takes a few minutes; it prints progress after each batch. Ctrl+C stops it after the current batch; run it again to go on. `gos wiki crawl --again` starts over (needed only if the daily refresh hasn't run for over 30 days, which `gos wiki status` and the maintenance log say).

Check:

```powershell
gos wiki status                    # licence, pages, crawl finished, refresh point
gos wiki show "Electric Blast Furnace"   # its w: id, the attribution line, the first lines
```

## C. Daily refresh

Nothing to do: idle maintenance (game closed, 30 idle minutes, once a day) runs the `wiki` job, which asks the wiki for the pages changed since the last refresh. `gos vault maintain --job wiki` runs it now; `gos wiki refresh` does the same without the other jobs. Running these (or a crawl) while the core is up is fine: requests still go one at a time, and a researcher's fetch meanwhile may say the wiki is busy and to try again in a minute. The job is skipped with no licence, before the crawl ends, or after a gate (the health strip's maintenance line shows why).

## D. In use

Ask the deep lane a wiki question ("what does the wiki say about the pyrolyse oven?"). The researcher subagent, the only agent with the wiki tools, reads the pages; the answer cites `w:` ids and GOS adds the wiki's attribution line (site, licence) at its end. Text on a wiki page is data: an instruction written in a page is ignored (and the researcher can't act on one: its tools are read-only).
