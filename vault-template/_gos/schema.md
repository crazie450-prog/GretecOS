---
id: gos-schema
t: schema
upd: 2026-11-02
---
version: 0
note: the single source of truth for vault writers (plan 6.2); WO-2.5 re-measures and freezes it
frontmatter: id, t, upd
fact: one per line as key: value
key_format: lowercase letters, digits and _; units go in the key (rate_s, eut, dur_t, eff_h)
lists: a, b, c
links: [[id]]
pointers: r:<map>/<sig12>, plan:<id>, q:<named_query>, db:<table>/<id>, w:<page>@<revid>
tables: tsv in a ```tsv fence with a header row
prose: none, except one-line note: entries
max_lines: 150
note: keys * means any key until the WO that owns the type defines them

```tsv
t	id	path	writer	keys
schema	gos-schema	_gos/schema.md	repo	*
index	index	INDEX.md	agents	*
goals	me-goals	me/goals.md	clayton	goal, note
prefs	me-prefs	me/prefs.md	clayton	*
tier	base-tier	base/tier.md	generated	*
power	base-power	base/power.md	agents	*
machines	base-machines	base/machines.md	generated	*
storage	base-storage	base/storage.md	generated	*
places	base-places	base/places.md	agents	*
line	ln-*	base/lines/	agents	st, tgt, tgt_rate_s, plan, eut, tier, deps, note
plan	pl-*	plans/	agents	*
quest	qs-*	quests/	agents	*
theme	th-*	themes/	agents	*
persona	pe-*	personas/	both	*
log	log-*	log/	agents	*
inbox	ib-*	inbox/	agents	*
```
