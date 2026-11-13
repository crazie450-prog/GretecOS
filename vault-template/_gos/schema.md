---
id: gos-schema
t: schema
upd: 2026-11-12
---
version: 0
summary: note types and allowed keys
note: the single source of truth for vault writers (plan 6.2); WO-2.5 re-measures and freezes it
frontmatter: id, t, upd
fact: one per line as key: value
key_format: lowercase letters, digits and _; units go in the key (rate_s, eut, dur_t, eff_h)
lists: a, b, c
links: [[id]]
pointers: r:{map}/{sig12}, plan:{id}, q:{named_query}, db:{table}/{id}, w:{page}@{revid}
tables: tsv in a ```tsv fence with a header row
diagrams: plan notes only: a flow diagram in a ```mermaid fence, only the lines the planner writes (header, boxes, labelled links; no click, href, style or %%), not facts (D201)
prose: none, except one-line note: entries
max_lines: 150 (INDEX.md: 200, plan notes: 40)
index_summary: INDEX.md shows a note's summary: fact, else its first title:, note: or other fact
log_counts: n_<kind>: N lines are the curator's computed counts, in log/wk/ and log/mo/ only; no other log uses an n_ key
note: keys * means any key until the WO that owns the type defines them
note: quest notes (WO-6.4): quests/qs-<line>.md are questline summaries made by code (gos quests summaries), at most 60 lines; agents add quest decisions as decision: lines in other qs-* notes
note: persona packs (WO-5.6): the shipped ones are in _gos/personas/ (the repo's), Clayton's in personas/pe-<name>.md

```tsv
t	id	path	writer	keys
schema	gos-schema	_gos/schema.md	repo	*
index	index	INDEX.md	generated	*
goals	me-goals	me/goals.md	clayton	goal, note
prefs	me-prefs	me/prefs.md	clayton	*
tier	base-tier	base/tier.md	generated	*
power	base-power	base/power.md	agents	title, lines, sources, gen_eut, use_eut_avg, use_eut_peak, net_eut, run_gen_eut, run_use_eut_avg, run_use_eut_peak, run_net_eut, warnings, tel, tel_eu_in, tel_eu_out, note
machines	base-machines	base/machines.md	generated	*
storage	base-storage	base/storage.md	generated	*
places	base-places	base/places.md	agents	*
line	ln-*	base/lines/	agents	st, tgt, tgt_rate_s, plan, eut, tier, deps, note
plan	pl-*	plans/	agents	title, st, plan, json, run, tgt, tgt_rate_s, tier, machines, eut_avg, eut_peak, eff_h, scarcity, area, volume, footprint_unknown, rank, alts, exports, note
quest	qs-*	quests/	agents	summary, line, done, available, locked, total, main_left, src, decision, note
theme	th-*	themes/	agents	*
persona	pe-*	personas/	both	name, voice, verbosity, humour, address, style, lexicon, sample, note
log	log-*	log/	agents	*
inbox	ib-*	inbox/	agents	*
```
