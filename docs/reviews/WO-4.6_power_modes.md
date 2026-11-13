# WO-4.6 power modes: 4 modes at LV, HV and IV

`gos eval power-modes` on the server harness DB: **12 of 12 cases pass**. Every number below is the power designer's (plan 7.2) on that DB, ranked by planner/rank.py with the balanced default weights (D67); the explanation checks use fake planner models (no key). Clayton's review of these options is a PC step.

| case | mode | tier | options | result | s |
|---|---|---|---|---|---|
| lv-best | best | LV | 3 | pass | 21 |
| lv-max | max_output | LV | 3 | pass | 24 |
| lv-effort | output_per_effort | LV | 3 | pass | 24 |
| lv-sustain | self_sustaining | LV | 3 | pass | 19 |
| hv-best | best | HV | 3 | pass | 43 |
| hv-max | max_output | HV | 3 | pass | 43 |
| hv-effort | output_per_effort | HV | 3 | pass | 54 |
| hv-sustain | self_sustaining | HV | 3 | pass | 71 |
| iv-best | best | IV | 3 | pass | 131 |
| iv-max | max_output | IV | 3 | pass | 118 |
| iv-effort | output_per_effort | IV | 3 | pass | 140 |
| iv-sustain | self_sustaining | IV | 3 | pass | 85 |

## lv-best: best at LV

Weights: mode best, ranked by the weighted score; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-f255b45118 | Large Gas Turbine on nitrobenzene, Steel large rotor | fluid:nitrobenzene | 1,215 | 1,215 | 0.01681 | LV x38 | 0.1612 | - | 0 | 10 | 72.52 | no |
| 2 | plan:pw-7d23f009f0 | Novice Magic Energy Converter on gregtech:gt.metaitem.01@17397 | gregtech:gt.metaitem.01@17397 | 32 | 32 | 0 | LV x1 | 0.001696 | - | 0 | 1 | 0.0004851 | no |
| 3 | plan:pw-ec5c4432cb | Novice Magic Energy Converter on gregtech:gt.metaitem.01@2397 | gregtech:gt.metaitem.01@2397 | 32 | 32 | 0.000002641 | LV x1 | -0.001875 | - | 0 | 2 | 0.0004851 | no |

Checks:
- options: pass (3 options)
- order: pass (positions 1..3, by score)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode best, ranked by the weighted score; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

5 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-f255b45118) burns fluid:nitrobenzene in its Large Gas Turbine on nitrobenzene, Steel large rotor: 1,215 EU/t net, score 0.1612, effort 0 h.
> Option 2 (plan:pw-7d23f009f0) burns gregtech:gt.metaitem.01@17397 in its Novice Magic Energy Converter on gregtech:gt.metaitem.01@17397: 32 EU/t net, score 0.001696, effort 0 h.
> Option 3 (plan:pw-ec5c4432cb) burns gregtech:gt.metaitem.01@2397 in its Novice Magic Energy Converter on gregtech:gt.metaitem.01@2397: 32 EU/t net, score -0.001875, effort 0 h.

## lv-max: max_output at LV

Weights: mode max_output, ranked by net EU/t within the scale bound; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-8032738057 | Large Gas Turbine on nitrobenzene, Steel large rotor | fluid:nitrobenzene | 1,215 | 1,215 | 0.01681 | LV x38 | 0.1612 | - | 0 | 10 | 72.52 | no |
| 2 | plan:pw-527ffe01d1 | Novice Magic Energy Converter on gregtech:gt.metaitem.01@17397 | gregtech:gt.metaitem.01@17397 | 32 | 32 | 0 | LV x1 | 0.001696 | - | 0 | 1 | 0.0004851 | no |
| 3 | plan:pw-6bd56c2860 | Large Plasma Turbine on plasma.bismuth, Steel large rotor | fluid:plasma.bismuth | 32 | 32 | 0 | LV x1 | -0.002186 | - | 0 | 4 | 7.2 | no |

Checks:
- options: pass (3 options)
- order: pass (positions 1..3, by net EU/t)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode max_output, ranked by net EU/t within the scale bound; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

Warnings: pin fluid:plasma.bismuth=r:gt.recipe.eyeofharmony/375086cab230 not available on base machines; fluid:plasma.bismuth imported; r:gt.recipe.eyeofharmony/375086cab230: every machine for it is custom, flagged, low_confidence

5 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-8032738057) burns fluid:nitrobenzene in its Large Gas Turbine on nitrobenzene, Steel large rotor: 1,215 EU/t net, score 0.1612, effort 0 h.
> Option 2 (plan:pw-527ffe01d1) burns gregtech:gt.metaitem.01@17397 in its Novice Magic Energy Converter on gregtech:gt.metaitem.01@17397: 32 EU/t net, score 0.001696, effort 0 h.
> Option 3 (plan:pw-6bd56c2860) burns fluid:plasma.bismuth in its Large Plasma Turbine on plasma.bismuth, Steel large rotor: 32 EU/t net, score -0.002186, effort 0 h.

## lv-effort: output_per_effort at LV

Weights: mode output_per_effort, ranked by net EU/t per effort hour; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-1b521ce0a8 | Basic Semifluid Generator on naphthenicacid | fluid:naphthenicacid | 31.98 | 32 | 0.01681 | LV x1 | -0.2311 | 124.5 | 0.2569 | 15 | 15.68 | no |
| 2 | plan:pw-de0be55eb0 | Basic Semifluid Generator on nefariousoil | fluid:nefariousoil | 31.98 | 32 | 0.01681 | LV x1 | -0.2307 | 124.5 | 0.2569 | 15 | 5.259 | no |
| 3 | plan:pw-f5d5f80350 | Basic Semifluid Generator on glycerol | fluid:glycerol | 29.26 | 32 | 2.739 | LV x1 | -0.2507 | 113.9 | 0.2569 | 14 | 540.8 | no |

Checks:
- options: pass (3 options)
- order: pass (positions 1..3, by net EU/t per effort hour (D235/D236 groups first))
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode output_per_effort, ranked by net EU/t per effort hour; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

5 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-1b521ce0a8) burns fluid:naphthenicacid in its Basic Semifluid Generator on naphthenicacid: 31.98 EU/t net, score -0.2311, effort 0.2569 h.
> Option 2 (plan:pw-de0be55eb0) burns fluid:nefariousoil in its Basic Semifluid Generator on nefariousoil: 31.98 EU/t net, score -0.2307, effort 0.2569 h.
> Option 3 (plan:pw-f5d5f80350) burns fluid:glycerol in its Basic Semifluid Generator on glycerol: 29.26 EU/t net, score -0.2507, effort 0.2569 h.

## lv-sustain: self_sustaining at LV

Weights: mode self_sustaining, ranked by the weighted score, closed lines only; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-a9686e1b85 | Basic Gas Turbine on methane | fluid:methane | 25.71 | 32 | 6.29 | LV x1 | 0.16 | - | 0 | 5 | 0 | yes |
| 2 | plan:pw-4783efe348 | Basic Gas Turbine on hydrogen | fluid:hydrogen | 15.77 | 32 | 16.23 | LV x1 | 0.08264 | - | 0 | 3 | 0 | yes |
| 3 | plan:pw-c24ebe1fc9 | Basic Gas Turbine on hydrogen | fluid:hydrogen | 15.63 | 32 | 16.37 | LV x1 | 0.04962 | - | 0 | 5 | 0 | yes |

Checks:
- options: pass (3 options)
- order: pass (positions 1..3, by score)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode self_sustaining, ranked by the weighted score, closed lines only; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (every option proven closed, with its kit)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

42 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-a9686e1b85) burns fluid:methane in its Basic Gas Turbine on methane: 25.71 EU/t net, score 0.16, effort 0 h.
> Option 2 (plan:pw-4783efe348) burns fluid:hydrogen in its Basic Gas Turbine on hydrogen: 15.77 EU/t net, score 0.08264, effort 0 h.
> Option 3 (plan:pw-c24ebe1fc9) burns fluid:hydrogen in its Basic Gas Turbine on hydrogen: 15.63 EU/t net, score 0.04962, effort 0 h.

## hv-best: best at HV

Weights: mode best, ranked by the weighted score; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-6048eab3be | Master Magic Energy Converter on gregtech:gt.metaitem.01@17397 | gregtech:gt.metaitem.01@17397 | 512 | 512 | 0 | HV x1 | 0.08294 | - | 0 | 1 | 0.008674 | no |
| 2 | plan:pw-054b346e50 | Master Magic Energy Converter on gregtech:gt.metaitem.01@2397 | gregtech:gt.metaitem.01@2397 | 512 | 512 | 0.00004722 | HV x1 | 0.08124 | - | 0 | 2 | 0.008674 | no |
| 3 | plan:pw-0b7a12b0db | Master Magic Energy Converter on gregtech:gt.metaitem.01@11397 | gregtech:gt.metaitem.01@11397 | 512 | 512 | 0.0004409 | HV x1 | 0.08124 | - | 0 | 2 | 0.008674 | no |

Checks:
- options: pass (3 options)
- order: pass (positions 1..3, by score)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode best, ranked by the weighted score; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

9 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-6048eab3be) burns gregtech:gt.metaitem.01@17397 in its Master Magic Energy Converter on gregtech:gt.metaitem.01@17397: 512 EU/t net, score 0.08294, effort 0 h.
> Option 2 (plan:pw-054b346e50) burns gregtech:gt.metaitem.01@2397 in its Master Magic Energy Converter on gregtech:gt.metaitem.01@2397: 512 EU/t net, score 0.08124, effort 0 h.
> Option 3 (plan:pw-0b7a12b0db) burns gregtech:gt.metaitem.01@11397 in its Master Magic Energy Converter on gregtech:gt.metaitem.01@11397: 512 EU/t net, score 0.08124, effort 0 h.

## hv-max: max_output at HV

Weights: mode max_output, ranked by net EU/t within the scale bound; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-b83d7e18ac | Large Gas Turbine on nitrobenzene, Steel large rotor | fluid:nitrobenzene | 1,210 | 1,215 | 5.039 | HV x3 | -0.02067 | 10,429 | 0.116 | 31 | 136.8 | no |
| 2 | plan:pw-41474e401d | Large Plasma Turbine on plasma.bismuth, Steel large rotor | fluid:plasma.bismuth | 512 | 512 | 0 | HV x1 | -0.1188 | 4,413 | 0.116 | 19 | 7.2 | no |
| 3 | plan:pw-f89b4a92a6 | Master Magic Energy Converter on gregtech:gt.metaitem.01@17397 | gregtech:gt.metaitem.01@17397 | 512 | 512 | 0 | HV x1 | 0.08294 | - | 0 | 1 | 0.008674 | no |

Checks:
- options: pass (3 options)
- order: pass (positions 1..3, by net EU/t)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode max_output, ranked by net EU/t within the scale bound; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

Warnings: pin fluid:plasma.bismuth=r:gt.recipe.eyeofharmony/375086cab230 not available on base machines; fluid:plasma.bismuth imported; r:gt.recipe.eyeofharmony/375086cab230: every machine for it is custom, flagged, low_confidence

9 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-b83d7e18ac) burns fluid:nitrobenzene in its Large Gas Turbine on nitrobenzene, Steel large rotor: 1,210 EU/t net, score -0.02067, effort 0.116 h.
> Option 2 (plan:pw-41474e401d) burns fluid:plasma.bismuth in its Large Plasma Turbine on plasma.bismuth, Steel large rotor: 512 EU/t net, score -0.1188, effort 0.116 h.
> Option 3 (plan:pw-f89b4a92a6) burns gregtech:gt.metaitem.01@17397 in its Master Magic Energy Converter on gregtech:gt.metaitem.01@17397: 512 EU/t net, score 0.08294, effort 0 h.

## hv-effort: output_per_effort at HV

Weights: mode output_per_effort, ranked by net EU/t per effort hour; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-5ae4308567 | Large Gas Turbine on nitrobenzene, Steel large rotor | fluid:nitrobenzene | 1,210 | 1,215 | 5.039 | HV x3 | 0.01431 | 10,429 | 0.116 | 31 | 136.8 | no |
| 2 | plan:pw-725f6fb7b2 | Turbo Semifluid Generator on nefariousoil | fluid:nefariousoil | 507 | 512 | 5.039 | HV x1 | -0.06409 | 5,669 | 0.08942 | 21 | 149.5 | no |
| 3 | plan:pw-3e18bb2607 | Turbo Semifluid Generator on glycerol | fluid:glycerol | 446.4 | 512 | 65.55 | HV x1 | -0.09201 | 4,993 | 0.08942 | 13 | 9,680 | no |

Checks:
- options: pass (3 options)
- order: pass (positions 1..3, by net EU/t per effort hour (D235/D236 groups first))
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode output_per_effort, ranked by net EU/t per effort hour; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

15 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-5ae4308567) burns fluid:nitrobenzene in its Large Gas Turbine on nitrobenzene, Steel large rotor: 1,210 EU/t net, score 0.01431, effort 0.116 h.
> Option 2 (plan:pw-725f6fb7b2) burns fluid:nefariousoil in its Turbo Semifluid Generator on nefariousoil: 507 EU/t net, score -0.06409, effort 0.08942 h.
> Option 3 (plan:pw-3e18bb2607) burns fluid:glycerol in its Turbo Semifluid Generator on glycerol: 446.4 EU/t net, score -0.09201, effort 0.08942 h.

## hv-sustain: self_sustaining at HV

Weights: mode self_sustaining, ranked by the weighted score, closed lines only; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-4c5d1929ba | Turbo Gas Turbine on methane | fluid:methane | 399.9 | 512 | 112.1 | HV x1 | 0.07744 | - | 0 | 5 | 0 | yes |
| 2 | plan:pw-5cd82db04f | Turbo Gas Turbine on hydrogen | fluid:hydrogen | 221.7 | 512 | 290.3 | HV x1 | -0.00659 | - | 0 | 3 | 0 | yes |
| 3 | plan:pw-dc98fd142d | Turbo Gas Turbine on hydrogen | fluid:hydrogen | 219.3 | 512 | 292.7 | HV x1 | -0.09659 | - | 0 | 5 | 0 | yes |

Checks:
- options: pass (3 options)
- order: pass (positions 1..3, by score)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode self_sustaining, ranked by the weighted score, closed lines only; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (every option proven closed, with its kit)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

143 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-4c5d1929ba) burns fluid:methane in its Turbo Gas Turbine on methane: 399.9 EU/t net, score 0.07744, effort 0 h.
> Option 2 (plan:pw-5cd82db04f) burns fluid:hydrogen in its Turbo Gas Turbine on hydrogen: 221.7 EU/t net, score -0.00659, effort 0 h.
> Option 3 (plan:pw-dc98fd142d) burns fluid:hydrogen in its Turbo Gas Turbine on hydrogen: 219.3 EU/t net, score -0.09659, effort 0 h.

## iv-best: best at IV

Weights: mode best, ranked by the weighted score; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-4dd625933e | Naquadah Reactor Mk-II on bartworks:gt.bwMetaGeneratedstick@89 | bartworks:gt.bwMetaGeneratedstick@89 | 8,189 | 8,192 | 3.438 | IV x1 | 0.1957 | - | 0 | 3 | 9.437 | no |
| 2 | plan:pw-92e04518a6 | Naquadah Reactor Mk-II on bartworks:gt.bwMetaGeneratedstick@89 | bartworks:gt.bwMetaGeneratedstick@89 | 8,189 | 8,192 | 3.438 | IV x1 | 0.1957 | - | 0 | 3 | 9.437 | no |
| 3 | plan:pw-6cc1b71445 | Turbo Geothermal Engine on lava | fluid:lava | 8,192 | 8,192 | 0 | IV x1 | 0.1932 | - | 0 | 1 | 29,491 | no |

Checks:
- options: pass (3 options)
- order: pass (positions 1..3, by score)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode best, ranked by the weighted score; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

Warnings: r:gt.recipe.multimixer/ef7463e8e51a: every machine for it is deprecated; r:gt.recipe.spaceMining/784e2403a891: every machine for it is custom, flagged, low_confidence

15 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-4dd625933e) burns bartworks:gt.bwMetaGeneratedstick@89 in its Naquadah Reactor Mk-II on bartworks:gt.bwMetaGeneratedstick@89: 8,189 EU/t net, score 0.1957, effort 0 h.
> Option 2 (plan:pw-92e04518a6) burns bartworks:gt.bwMetaGeneratedstick@89 in its Naquadah Reactor Mk-II on bartworks:gt.bwMetaGeneratedstick@89: 8,189 EU/t net, score 0.1957, effort 0 h.
> Option 3 (plan:pw-6cc1b71445) burns fluid:lava in its Turbo Geothermal Engine on lava: 8,192 EU/t net, score 0.1932, effort 0 h.

## iv-max: max_output at IV

Weights: mode max_output, ranked by net EU/t within the scale bound; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-43f2b4ce6a | Turbo Geothermal Engine on lava | fluid:lava | 8,192 | 8,192 | 0 | IV x1 | 0.1932 | - | 0 | 1 | 29,491 | no |
| 2 | plan:pw-9b7dbd5384 | Large Plasma Turbine on plasma.bismuth, Steel large rotor | fluid:plasma.bismuth | 8,192 | 8,192 | 0 | IV x1 | 0.1731 | 100,525 | 0.08149 | 16 | 7.2 | no |
| 3 | plan:pw-afd69294e3 | Turbo Geothermal Engine on gregtech:gt.metaitem.01@2843 | gregtech:gt.metaitem.01@2843 | 8,192 | 8,192 | 0.008 | IV x1 | 0.1924 | - | 0 | 2 | 14,636 | no |

Checks:
- options: pass (3 options)
- order: pass (positions 1..3, by net EU/t)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode max_output, ranked by net EU/t within the scale bound; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

Warnings: pin fluid:plasma.bismuth=r:gt.recipe.eyeofharmony/375086cab230 not available on base machines; fluid:plasma.bismuth imported; r:gt.recipe.eyeofharmony/375086cab230: every machine for it is custom, flagged, low_confidence

15 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-43f2b4ce6a) burns fluid:lava in its Turbo Geothermal Engine on lava: 8,192 EU/t net, score 0.1932, effort 0 h.
> Option 2 (plan:pw-9b7dbd5384) burns fluid:plasma.bismuth in its Large Plasma Turbine on plasma.bismuth, Steel large rotor: 8,192 EU/t net, score 0.1731, effort 0.08149 h.
> Option 3 (plan:pw-afd69294e3) burns gregtech:gt.metaitem.01@2843 in its Turbo Geothermal Engine on gregtech:gt.metaitem.01@2843: 8,192 EU/t net, score 0.1924, effort 0 h.

## iv-effort: output_per_effort at IV

Weights: mode output_per_effort, ranked by net EU/t per effort hour; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-b1bb1ba078 | Large Plasma Turbine on plasma.bismuth, Steel large rotor | fluid:plasma.bismuth | 8,192 | 8,192 | 0 | IV x1 | 0.1736 | 100,525 | 0.08149 | 16 | 7.2 | no |
| 2 | plan:pw-e6d1994c5f | Turbo Semifluid Generator III on glycerol | fluid:glycerol | 7,071 | 8,192 | 1,121 | IV x1 | 0.1083 | 72,475 | 0.09756 | 13 | 175,029 | no |
| 3 | plan:pw-86818db1a7 | Turbo Semifluid Generator III on glycerol | fluid:glycerol | 6,878 | 8,192 | 1,314 | IV x1 | 0.0873 | 70,497 | 0.09756 | 18 | 201,448 | no |

Checks:
- options: pass (3 options)
- order: pass (positions 1..3, by net EU/t per effort hour (D235/D236 groups first))
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode output_per_effort, ranked by net EU/t per effort hour; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

Warnings: pin fluid:plasma.bismuth=r:gt.recipe.eyeofharmony/375086cab230 not available on base machines; fluid:plasma.bismuth imported; r:gt.recipe.eyeofharmony/375086cab230: every machine for it is custom, flagged, low_confidence

29 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-b1bb1ba078) burns fluid:plasma.bismuth in its Large Plasma Turbine on plasma.bismuth, Steel large rotor: 8,192 EU/t net, score 0.1736, effort 0.08149 h.
> Option 2 (plan:pw-e6d1994c5f) burns fluid:glycerol in its Turbo Semifluid Generator III on glycerol: 7,071 EU/t net, score 0.1083, effort 0.09756 h.
> Option 3 (plan:pw-86818db1a7) burns fluid:glycerol in its Turbo Semifluid Generator III on glycerol: 6,878 EU/t net, score 0.0873, effort 0.09756 h.

## iv-sustain: self_sustaining at IV

Weights: mode self_sustaining, ranked by the weighted score, closed lines only; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-024689f9f0 | Turbo Gas Turbine III on methane | fluid:methane | 5,157 | 8,192 | 3,035 | IV x1 | 0.08889 | - | 0 | 5 | 0 | yes |
| 2 | plan:pw-e5283c1ba1 | Turbo Gas Turbine III on hydrogen | fluid:hydrogen | 295.7 | 8,192 | 7,896 | IV x1 | -0.09964 | - | 0 | 3 | 0 | yes |
| 3 | plan:pw-6e500e8eda | Basic Gas Turbine on methane | fluid:methane | 25.71 | 32 | 6.29 | LV x1 | -0.1101 | - | 0 | 5 | 0 | yes |

Checks:
- options: pass (3 options)
- order: pass (positions 1..3, by score)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode self_sustaining, ranked by the weighted score, closed lines only; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (every option proven closed, with its kit)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

178 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-024689f9f0) burns fluid:methane in its Turbo Gas Turbine III on methane: 5,157 EU/t net, score 0.08889, effort 0 h.
> Option 2 (plan:pw-e5283c1ba1) burns fluid:hydrogen in its Turbo Gas Turbine III on hydrogen: 295.7 EU/t net, score -0.09964, effort 0 h.
> Option 3 (plan:pw-6e500e8eda) burns fluid:methane in its Basic Gas Turbine on methane: 25.71 EU/t net, score -0.1101, effort 0 h.
