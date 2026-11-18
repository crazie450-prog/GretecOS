# Runbook: moving the core to the spare PC (later; plan §4.2 "Moving later", D45)

Day one runs everything on the gaming PC (D03, D06, D07). This is the procedure for moving the
core, the DB, the vault and the Discord bot to the spare PC (or a dedicated server) later.
**Nothing here is built before day one (D45):** the three pieces of work below are future work
orders. What exists now is this document and a read-only config check:

```powershell
gos relocate check                            # today's layout: what holds, what the move needs
gos relocate check --core-ip 192.168.1.20     # with the spare PC's LAN address
gos relocate check --oc-config <path>\OpenComputers.cfg
```

It prints one line per step below (`ok`, `todo`, `problem`) and exits 1 on a `problem`
(`gregtec.ops.relocation`, D335). It writes nothing.

## The three pieces of work (plan §4.2)

1. **A client forwarder on the gaming PC.** Logs, JourneyMap `mp` data and the VisualProspecting
   client cache stay on the client [B§8], so a small process there reads them as the watchers do
   now (through `safe_open`) and posts them to the core with the watchers' token. It runs from
   GOS_APP under the gaming PC's own supervisor, with Play.
2. **OC's rule for the core's LAN address, first.** GTNH's shipped `filteringRules` deny
   `192.168.0.0/16` [C§1.3], so `allow ip:<core LAN IP>` must be the first entry (it replaces
   today's `allow ip:127.0.0.1`; on a dedicated server running the world, `127.0.0.1` is the
   server and stays). `check_tweaks.py` gains the address as a parameter; Play's copy is changed
   by Clayton's own script, as WO-0.6's tweak is.
3. **The core on the LAN, with a token and a firewall rule** (a documented exception to §4.6).
   Today `config.py` refuses any `[core] host` but `127.0.0.1`. The work adds the LAN listener:
   every request needs a bearer token or the paired cookie (no unauthenticated route on the LAN),
   Host/Origin checks gain the LAN name, and a Windows firewall rule allows the port from the
   gaming PC's address only. Funnel stays off; the phone keeps using Tailscale Serve (D53).

GregtecLink's listener module must also be installed on the server when the world moves there.

## Config keys that change

| file / key | today | after the move |
|---|---|---|
| spare PC `gos.toml` `[core] host` | `127.0.0.1` | the LAN address (needs work item 3) |
| spare PC `[core] allowed_hosts` | loopback, the tailnet name | + `<lan-ip>:<port>`; the spare PC's tailnet name (run `gos tailnet setup` there, D301) |
| spare PC `[paths] world` / `prism_instances` | Play's world on this PC | the server's world folder (dedicated server) or none (client-hosted: the forwarder reads it) |
| gaming PC: voice, the forwarder, GregtecLink `core_url` | `http://127.0.0.1:8765` | `http://<lan-ip>:<port>` |
| gaming PC `[mailbox.play]` | Play's world | unchanged on a client-hosted world; the server's world otherwise |
| `OpenComputers.cfg` first `filteringRules` entry | `allow ip:127.0.0.1` | `allow ip:<lan-ip>` (client-hosted) |
| Windows firewall on the spare PC | none | inbound TCP `<port>` from the gaming PC only |
| Credential Manager on the spare PC | none | the Anthropic key, the Discord token, the client tokens (re-issued with `gos token new`) |

## Order

1. Build and merge the three work items; test them on Lab with the spare PC on the LAN.
2. `gos relocate check --core-ip <lan-ip> --oc-config <Lab's OpenComputers.cfg>`: no `problem`.
3. On the gaming PC: `gos stop --all`. `gos vault maintain --job backup`, then
   `gos backup restore --verify` (ops.md step 1): the backups are good.
4. Install GOS on the spare PC (`gos deploy` from a clone there), copy `data\` and `vault\`
   from the gaming PC's GOS_HOME (or restore them with `gos backup restore --to`), and set the
   keys above. Store its secrets (`gos key set`, `gos key set discord`, `gos token new ...`).
5. Start the spare PC's supervisor; `gos doctor` and the health strip there are green.
6. On the gaming PC: point the clients' `core_url` at the spare PC, change OC's first rule (the
   Play script), start the forwarder with Play. Lab first, then Play.
7. Check: the dashboard on monitor 2 and the phone, an OC message arriving, a confirmed Lab
   action, a Discord DM (`gos alerts test`), voice answering.

## Rollback

Everything on the gaming PC is kept until the move has run for a week:

1. Stop the spare PC's supervisor.
2. On the gaming PC: put `core_url` back to `http://127.0.0.1:8765`, OC's first rule back to
   `allow ip:127.0.0.1` (`check_tweaks.py`, Play by Clayton's script), stop the forwarder.
3. Copy the spare PC's `data\` and `vault\` back if they changed (or keep the gaming PC's copies
   if nothing important happened), then start the gaming PC's supervisor (log off and on).
4. `gos relocate check` shows today's layout again with no `problem`.
