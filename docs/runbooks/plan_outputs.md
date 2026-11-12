# Plan outputs (WO-3.6)

A design saved with `gos plan <target> --tier <T> --qty <n>/s --save` (WO-3.7's designer saves the
same way) leaves, in GOS_HOME:

| file | what |
|---|---|
| `data/plans/<id>.json` | the canonical plan (source of truth), with its `layout` (footprints, D200) |
| `data/plans/<id>/bom.tsv` | the BOM: machine, tier, count, OC, parallel, EU/t, utilisation, `r:` id, totals |
| `data/plans/<id>/flow.mmd`, `flow.png` | the flow diagram (Mermaid; PNG for Discord, D201) |
| `data/plans/<id>/gtnhplanner.json` | the gtnhplanner.com project (D66, D202) |
| `data/plans/<id>/gtnh-flow.yaml` | the gtnh-flow project (D202) |
| `vault/plans/<id>.md` | the vault note: summary facts, machines by type, the Mermaid diagram |

plus a `plan` row (status `proposed`). The deep lane reads them with `plan_export`; the dashboard and
Discord with `GET /api/plans/<id>/cards` and `GET /api/plans/<id>/export/<format>`.

## Clayton's PC checks (deployment test)

1. **gtnhplanner.com import** (checked from the cloud container on 2026-11-12 for three G3 lines;
   repeat once on the PC): open https://gtnhplanner.com, close the "now in the game" and tour
   pop-ups, open the plan menu ("Plan menu: share, import, export") → **Import a plan...**, pick
   `%GOS_HOME%\data\plans\<id>\gtnhplanner.json`. A new tab named like the plan opens; the
   overview lists the plan's inputs and outputs, and each card shows GOS's machine count in its
   `≤` box. The site sizes machines with its own overclock math, so its MACHINES total may differ
   from GOS's (D202). Or, from a clone with the dashboard installed:
   `cd dashboard; node e2e/gtnhplanner-import.cjs "$env:GOS_HOME\data\plans\<id>\gtnhplanner.json"`.
2. **gtnh-flow render** (not run in the cloud container, D202). In PowerShell, in a scratch folder
   outside the repo and outside every Minecraft folder:

   ```powershell
   $ProgressPreference = 'SilentlyContinue'
   winget install --id Graphviz.Graphviz -e      # gtnh-flow draws with Graphviz's dot
   git clone https://github.com/OrderedSet86/gtnh-flow.git
   cd gtnh-flow
   py -3.12 -m venv .venv; .\.venv\Scripts\pip install -r requirements.txt
   mkdir projects\gos
   copy "$env:GOS_HOME\data\plans\<id>\gtnh-flow.yaml" projects\gos\<id>.yaml
   .\.venv\Scripts\python factory_graph.py --no_view_on_completion gos/<id>.yaml
   ```

   Pass: `output\gos\<id>.png` exists and shows one box per GOS machine, with machine counts equal
   to the BOM's before rounding up (e.g. 22.7 for a node GOS sizes at 23).
