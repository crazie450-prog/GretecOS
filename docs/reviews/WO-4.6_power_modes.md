# WO-4.6 power modes: 4 modes at LV, HV and IV

`gos eval power-modes` on the server harness DB: **12 of 12 cases pass**. Every number below is the power designer's (plan 7.2) on that DB, ranked by planner/rank.py with the balanced default weights (D67); the explanation checks use fake planner models (no key). Clayton's review of these options is a PC step.

| case | mode | tier | options | result | s |
|---|---|---|---|---|---|
| lv-best | best | LV | 3 | pass | 25 |
| lv-max | max_output | LV | 3 | pass | 23 |
| lv-effort | output_per_effort | LV | 3 | pass | 25 |
| lv-sustain | self_sustaining | LV | 3 | pass | 21 |
| hv-best | best | HV | 3 | pass | 49 |
| hv-max | max_output | HV | 3 | pass | 48 |
| hv-effort | output_per_effort | HV | 3 | pass | 57 |
| hv-sustain | self_sustaining | HV | 3 | pass | 72 |
| iv-best | best | IV | 3 | pass | 99 |
| iv-max | max_output | IV | 3 | pass | 112 |
| iv-effort | output_per_effort | IV | 3 | pass | 137 |
| iv-sustain | self_sustaining | IV | 3 | pass | 81 |

## lv-best: best at LV

Weights: mode best, ranked by the weighted score; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-c8477b6d44 | Large Gas Turbine on nitrobenzene, Steel large rotor | Nitrobenzene (fluid:nitrobenzene) | 1,215 | 1,215 | 0.01681 | LV x38 | 0.1612 | - | 0 | 10 | 72.52 | no |
| 2 | plan:pw-7488f89bbb | Novice Magic Energy Converter on gregtech:gt.metaitem.01@17397 | Infinity Plate (gregtech:gt.metaitem.01@17397) | 32 | 32 | 0 | LV x1 | 0.001696 | - | 0 | 1 | 0.0004851 | no |
| 3 | plan:pw-ad5fbc5e21 | Novice Magic Energy Converter on gregtech:gt.metaitem.01@2397 | Infinity Dust (gregtech:gt.metaitem.01@2397) | 32 | 32 | 0.000002641 | LV x1 | -0.001875 | - | 0 | 2 | 0.0004851 | no |

Checks:
- options: pass (3 options)
- distinct: pass (no two options the same line)
- order: pass (positions 1..3, by score)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode best, ranked by the weighted score; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

How each option differs from option 1: option 2: generator Novice Magic Energy Converter on gregtech:gt.metaitem.01@17397; fuel Infinity Plate; 6 of option 1's recipe(s) not used; option 3: generator Novice Magic Energy Converter on gregtech:gt.metaitem.01@2397; fuel Infinity Dust; 1 recipe(s) option 1 doesn't use; 6 of option 1's recipe(s) not used (uses r:gt.recipe.macerator/2cf7bb9bc04a; not r:gt.recipe.canner/22e615ebacc1, r:gt.recipe.canner/7dc9394d9318, r:gt.recipe.canner/87ff2d4e728e, r:gt.recipe.canner/8847f7d571ef ...).

Effort 0 h with parts not counted: option 1 (10 not counted); option 2 (1 not counted); option 3 (2 not counted). The effort term can't tell these options apart (WO-4.3's effort numbers).

5 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-c8477b6d44) burns Nitrobenzene in its Large Gas Turbine on nitrobenzene, Steel large rotor: 1,215 EU/t net, score 0.1612, effort 0 h.
> Option 2 (plan:pw-7488f89bbb) burns Infinity Plate in its Novice Magic Energy Converter on gregtech:gt.metaitem.01@17397: 32 EU/t net, score 0.001696, effort 0 h.
> Option 3 (plan:pw-ad5fbc5e21) burns Infinity Dust in its Novice Magic Energy Converter on gregtech:gt.metaitem.01@2397: 32 EU/t net, score -0.001875, effort 0 h.

## lv-max: max_output at LV

Weights: mode max_output, ranked by net EU/t within the scale bound; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-b64fe49a79 | Large Gas Turbine on nitrobenzene, Steel large rotor | Nitrobenzene (fluid:nitrobenzene) | 1,215 | 1,215 | 0.01681 | LV x38 | 0.1612 | - | 0 | 10 | 72.52 | no |
| 2 | plan:pw-5be3e4feb4 | Novice Magic Energy Converter on gregtech:gt.metaitem.01@17397 | Infinity Plate (gregtech:gt.metaitem.01@17397) | 32 | 32 | 0 | LV x1 | 0.001696 | - | 0 | 1 | 0.0004851 | no |
| 3 | plan:pw-a89b411789 | Large Plasma Turbine on plasma.bismuth, Steel large rotor | Bismuth Plasma (fluid:plasma.bismuth) | 32 | 32 | 0 | LV x1 | -0.002186 | - | 0 | 4 | 7.2 | no |

Checks:
- options: pass (3 options)
- distinct: pass (no two options the same line)
- order: pass (positions 1..3, by net EU/t)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode max_output, ranked by net EU/t within the scale bound; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

How each option differs from option 1: option 2: generator Novice Magic Energy Converter on gregtech:gt.metaitem.01@17397; fuel Infinity Plate; 6 of option 1's recipe(s) not used; option 3: generator Large Plasma Turbine on plasma.bismuth, Steel large rotor; fuel Bismuth Plasma; 6 of option 1's recipe(s) not used.

Effort 0 h with parts not counted: option 1 (10 not counted); option 2 (1 not counted); option 3 (4 not counted). The effort term can't tell these options apart (WO-4.3's effort numbers).

Warnings: pin fluid:plasma.bismuth=r:gt.recipe.eyeofharmony/375086cab230 not available on base machines; fluid:plasma.bismuth imported; r:gt.recipe.eyeofharmony/375086cab230: every machine for it is custom, flagged, low_confidence

5 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-b64fe49a79) burns Nitrobenzene in its Large Gas Turbine on nitrobenzene, Steel large rotor: 1,215 EU/t net, score 0.1612, effort 0 h.
> Option 2 (plan:pw-5be3e4feb4) burns Infinity Plate in its Novice Magic Energy Converter on gregtech:gt.metaitem.01@17397: 32 EU/t net, score 0.001696, effort 0 h.
> Option 3 (plan:pw-a89b411789) burns Bismuth Plasma in its Large Plasma Turbine on plasma.bismuth, Steel large rotor: 32 EU/t net, score -0.002186, effort 0 h.

## lv-effort: output_per_effort at LV

Weights: mode output_per_effort, ranked by net EU/t per effort hour; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-4d74e8b0fb | Basic Semifluid Generator on naphthenicacid | Naphthenic Acid (fluid:naphthenicacid) | 31.98 | 32 | 0.01681 | LV x1 | -0.2311 | 124.5 | 0.2569 | 15 | 15.68 | no |
| 2 | plan:pw-a98b349d9b | Basic Semifluid Generator on nefariousoil | Nefarious Oil (fluid:nefariousoil) | 31.98 | 32 | 0.01681 | LV x1 | -0.2307 | 124.5 | 0.2569 | 15 | 5.259 | no |
| 3 | plan:pw-c956a28d70 | Basic Semifluid Generator on glycerol | Glycerol (fluid:glycerol) | 29.26 | 32 | 2.739 | LV x1 | -0.2507 | 113.9 | 0.2569 | 14 | 540.8 | no |

Checks:
- options: pass (3 options)
- distinct: pass (no two options the same line)
- order: pass (positions 1..3, by net EU/t per effort hour (D235/D236 groups first))
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode output_per_effort, ranked by net EU/t per effort hour; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

How each option differs from option 1: option 2: generator Basic Semifluid Generator on nefariousoil; fuel Nefarious Oil; 2 recipe(s) option 1 doesn't use; 2 of option 1's recipe(s) not used (uses r:gt.recipe.canner/638b7f222260, r:gt.recipe.canner/917fac298edb; not r:gt.recipe.canner/450c3fe04d2e, r:gt.recipe.canner/4f477e45e2fb); option 3: generator Basic Semifluid Generator on glycerol; fuel Glycerol; 4 recipe(s) option 1 doesn't use; 6 of option 1's recipe(s) not used (uses r:gt.recipe.fluidextractor/c2b1d96b12a7, r:gt.recipe.fluidextractor/cef0647aa230, r:gt.recipe.largechemicalreactor/7cdb610e5354, r:gt.recipe.multidehydrator/f56b80010a10; not r:gt.recipe.canner/22e615ebacc1, r:gt.recipe.canner/450c3fe04d2e, r:gt.recipe.canner/4f477e45e2fb, r:gt.recipe.canner/7dc9394d9318 ...).

5 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-4d74e8b0fb) burns Naphthenic Acid in its Basic Semifluid Generator on naphthenicacid: 31.98 EU/t net, score -0.2311, effort 0.2569 h.
> Option 2 (plan:pw-a98b349d9b) burns Nefarious Oil in its Basic Semifluid Generator on nefariousoil: 31.98 EU/t net, score -0.2307, effort 0.2569 h.
> Option 3 (plan:pw-c956a28d70) burns Glycerol in its Basic Semifluid Generator on glycerol: 29.26 EU/t net, score -0.2507, effort 0.2569 h.

## lv-sustain: self_sustaining at LV

Weights: mode self_sustaining, ranked by the weighted score, closed lines only; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-94e60c0035 | Basic Gas Turbine on methane | Methane (fluid:methane) | 25.71 | 32 | 6.29 | LV x1 | 0.1 | - | 0 | 5 | 0 | yes |
| 2 | plan:pw-0dc2f938fe | Basic Gas Turbine on hydrogen | Hydrogen (fluid:hydrogen) | 15.77 | 32 | 16.23 | LV x1 | 0.02264 | - | 0 | 3 | 0 | yes |
| 3 | plan:pw-7a37ffd526 | Basic Gas Turbine on hydrogen | Hydrogen (fluid:hydrogen) | 15.63 | 32 | 16.37 | LV x1 | -0.05838 | - | 0 | 5 | 0 | yes |

Checks:
- options: pass (3 options)
- distinct: pass (no two options the same line)
- order: pass (positions 1..3, by score)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode self_sustaining, ranked by the weighted score, closed lines only; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (every option proven closed, with its kit)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

How each option differs from option 1: option 2: generator Basic Gas Turbine on hydrogen; fuel Hydrogen; 1 recipe(s) option 1 doesn't use; 1 of option 1's recipe(s) not used (uses r:gt.recipe.multielectro/599bf7b376e7; not r:gt.recipe.multicentrifuge/295ed279224a); option 3: generator Basic Gas Turbine on hydrogen; fuel Hydrogen; 3 recipe(s) option 1 doesn't use; 1 of option 1's recipe(s) not used (uses r:gt.recipe.largechemicalreactor/9822ee3a5138, r:gt.recipe.multielectro/599bf7b376e7, r:gt.recipe.multielectro/604242dd79a0; not r:gt.recipe.multicentrifuge/295ed279224a).

Effort 0 h with parts not counted: option 1 (5 not counted); option 2 (3 not counted); option 3 (5 not counted). The effort term can't tell these options apart (WO-4.3's effort numbers).

42 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-94e60c0035) burns Methane in its Basic Gas Turbine on methane: 25.71 EU/t net, score 0.1, effort 0 h.
> Option 2 (plan:pw-0dc2f938fe) burns Hydrogen in its Basic Gas Turbine on hydrogen: 15.77 EU/t net, score 0.02264, effort 0 h.
> Option 3 (plan:pw-7a37ffd526) burns Hydrogen in its Basic Gas Turbine on hydrogen: 15.63 EU/t net, score -0.05838, effort 0 h.

## hv-best: best at HV

Weights: mode best, ranked by the weighted score; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-0ba8d9f7ef | Master Magic Energy Converter on gregtech:gt.metaitem.01@17397 | Infinity Plate (gregtech:gt.metaitem.01@17397) | 512 | 512 | 0 | HV x1 | 0.08294 | - | 0 | 1 | 0.008674 | no |
| 2 | plan:pw-a730b4275a | Master Magic Energy Converter on gregtech:gt.metaitem.01@2397 | Infinity Dust (gregtech:gt.metaitem.01@2397) | 512 | 512 | 0.00004722 | HV x1 | 0.08124 | - | 0 | 2 | 0.008674 | no |
| 3 | plan:pw-6efb5578fb | Master Magic Energy Converter on gregtech:gt.metaitem.01@11397 | Infinity Ingot (gregtech:gt.metaitem.01@11397) | 512 | 512 | 0.0004409 | HV x1 | 0.08124 | - | 0 | 2 | 0.008674 | no |

Checks:
- options: pass (3 options)
- distinct: pass (no two options the same line)
- order: pass (positions 1..3, by score)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode best, ranked by the weighted score; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

How each option differs from option 1: option 2: generator Master Magic Energy Converter on gregtech:gt.metaitem.01@2397; fuel Infinity Dust; 1 recipe(s) option 1 doesn't use (uses r:gt.recipe.macerator/2cf7bb9bc04a; not ); option 3: generator Master Magic Energy Converter on gregtech:gt.metaitem.01@11397; fuel Infinity Ingot; 1 recipe(s) option 1 doesn't use (uses r:gt.recipe.arcfurnace/38bb395abd22; not ).

Effort 0 h with parts not counted: option 1 (1 not counted); option 2 (2 not counted); option 3 (2 not counted). The effort term can't tell these options apart (WO-4.3's effort numbers).

9 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-0ba8d9f7ef) burns Infinity Plate in its Master Magic Energy Converter on gregtech:gt.metaitem.01@17397: 512 EU/t net, score 0.08294, effort 0 h.
> Option 2 (plan:pw-a730b4275a) burns Infinity Dust in its Master Magic Energy Converter on gregtech:gt.metaitem.01@2397: 512 EU/t net, score 0.08124, effort 0 h.
> Option 3 (plan:pw-6efb5578fb) burns Infinity Ingot in its Master Magic Energy Converter on gregtech:gt.metaitem.01@11397: 512 EU/t net, score 0.08124, effort 0 h.

## hv-max: max_output at HV

Weights: mode max_output, ranked by net EU/t within the scale bound; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-151b492ecd | Large Gas Turbine on nitrobenzene, Steel large rotor | Nitrobenzene (fluid:nitrobenzene) | 1,210 | 1,215 | 5.039 | HV x3 | -0.02067 | 10,429 | 0.116 | 31 | 136.8 | no |
| 2 | plan:pw-b3f5108c2b | Master Magic Energy Converter on gregtech:gt.metaitem.01@17397 | Infinity Plate (gregtech:gt.metaitem.01@17397) | 512 | 512 | 0 | HV x1 | 0.08294 | - | 0 | 1 | 0.008674 | no |
| 3 | plan:pw-ed36fc4e06 | Large Plasma Turbine on plasma.bismuth, Steel large rotor | Bismuth Plasma (fluid:plasma.bismuth) | 512 | 512 | 0 | HV x1 | -0.1188 | 4,413 | 0.116 | 19 | 7.2 | no |

Checks:
- options: pass (3 options)
- distinct: pass (no two options the same line)
- order: pass (positions 1..3, by net EU/t)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode max_output, ranked by net EU/t within the scale bound; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

How each option differs from option 1: option 2: generator Master Magic Energy Converter on gregtech:gt.metaitem.01@17397; fuel Infinity Plate; 8 of option 1's recipe(s) not used; option 3: generator Large Plasma Turbine on plasma.bismuth, Steel large rotor; fuel Bismuth Plasma; 8 of option 1's recipe(s) not used.

Effort 0 h with parts not counted: option 2 (1 not counted). The effort term can't tell these options apart (WO-4.3's effort numbers).

Warnings: pin fluid:plasma.bismuth=r:gt.recipe.eyeofharmony/375086cab230 not available on base machines; fluid:plasma.bismuth imported; r:gt.recipe.eyeofharmony/375086cab230: every machine for it is custom, flagged, low_confidence

9 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-151b492ecd) burns Nitrobenzene in its Large Gas Turbine on nitrobenzene, Steel large rotor: 1,210 EU/t net, score -0.02067, effort 0.116 h.
> Option 2 (plan:pw-b3f5108c2b) burns Infinity Plate in its Master Magic Energy Converter on gregtech:gt.metaitem.01@17397: 512 EU/t net, score 0.08294, effort 0 h.
> Option 3 (plan:pw-ed36fc4e06) burns Bismuth Plasma in its Large Plasma Turbine on plasma.bismuth, Steel large rotor: 512 EU/t net, score -0.1188, effort 0.116 h.

## hv-effort: output_per_effort at HV

Weights: mode output_per_effort, ranked by net EU/t per effort hour; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-517be49a22 | Large Gas Turbine on nitrobenzene, Steel large rotor | Nitrobenzene (fluid:nitrobenzene) | 1,210 | 1,215 | 5.039 | HV x3 | 0.01431 | 10,429 | 0.116 | 31 | 136.8 | no |
| 2 | plan:pw-f2e9a9c81f | Turbo Semifluid Generator on nefariousoil | Nefarious Oil (fluid:nefariousoil) | 507 | 512 | 5.039 | HV x1 | -0.06409 | 5,669 | 0.08942 | 21 | 149.5 | no |
| 3 | plan:pw-61f5baad91 | Turbo Semifluid Generator on glycerol | Glycerol (fluid:glycerol) | 446.4 | 512 | 65.55 | HV x1 | -0.09201 | 4,993 | 0.08942 | 13 | 9,680 | no |

Checks:
- options: pass (3 options)
- distinct: pass (no two options the same line)
- order: pass (positions 1..3, by net EU/t per effort hour (D235/D236 groups first))
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode output_per_effort, ranked by net EU/t per effort hour; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

How each option differs from option 1: option 2: generator Turbo Semifluid Generator on nefariousoil; fuel Nefarious Oil; 2 recipe(s) option 1 doesn't use; 2 of option 1's recipe(s) not used (uses r:gt.recipe.canner/638b7f222260, r:gt.recipe.canner/917fac298edb; not r:gt.recipe.canner/87ff2d4e728e, r:gt.recipe.canner/8847f7d571ef); option 3: generator Turbo Semifluid Generator on glycerol; fuel Glycerol; 4 recipe(s) option 1 doesn't use; 8 of option 1's recipe(s) not used (uses r:gt.recipe.chemicaldehydrator/5a449f211d5d, r:gt.recipe.fluidextractor/c2b1d96b12a7, r:gt.recipe.fluidextractor/cef0647aa230, r:gt.recipe.largechemicalreactor/7cdb610e5354; not r:gt.recipe.alloyblastsmelter/7fb2ec802e91, r:gt.recipe.alloysmelter/1d5e67877281, r:gt.recipe.canner/87ff2d4e728e, r:gt.recipe.canner/8847f7d571ef ...).

15 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-517be49a22) burns Nitrobenzene in its Large Gas Turbine on nitrobenzene, Steel large rotor: 1,210 EU/t net, score 0.01431, effort 0.116 h.
> Option 2 (plan:pw-f2e9a9c81f) burns Nefarious Oil in its Turbo Semifluid Generator on nefariousoil: 507 EU/t net, score -0.06409, effort 0.08942 h.
> Option 3 (plan:pw-61f5baad91) burns Glycerol in its Turbo Semifluid Generator on glycerol: 446.4 EU/t net, score -0.09201, effort 0.08942 h.

## hv-sustain: self_sustaining at HV

Weights: mode self_sustaining, ranked by the weighted score, closed lines only; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-afb1dcb57b | Turbo Gas Turbine on methane | Methane (fluid:methane) | 399.9 | 512 | 112.1 | HV x1 | 0.07744 | - | 0 | 5 | 0 | yes |
| 2 | plan:pw-268393bbe1 | Turbo Gas Turbine on hydrogen | Hydrogen (fluid:hydrogen) | 221.7 | 512 | 290.3 | HV x1 | -0.00659 | - | 0 | 3 | 0 | yes |
| 3 | plan:pw-6652815f82 | Turbo Gas Turbine on hydrogen | Hydrogen (fluid:hydrogen) | 219.3 | 512 | 292.7 | HV x1 | -0.09659 | - | 0 | 5 | 0 | yes |

Checks:
- options: pass (3 options)
- distinct: pass (no two options the same line)
- order: pass (positions 1..3, by score)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode self_sustaining, ranked by the weighted score, closed lines only; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (every option proven closed, with its kit)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

How each option differs from option 1: option 2: generator Turbo Gas Turbine on hydrogen; fuel Hydrogen; 1 recipe(s) option 1 doesn't use; 1 of option 1's recipe(s) not used (uses r:gt.recipe.multielectro/599bf7b376e7; not r:gt.recipe.multicentrifuge/295ed279224a); option 3: generator Turbo Gas Turbine on hydrogen; fuel Hydrogen; 3 recipe(s) option 1 doesn't use; 1 of option 1's recipe(s) not used (uses r:gt.recipe.largechemicalreactor/9822ee3a5138, r:gt.recipe.multielectro/599bf7b376e7, r:gt.recipe.multielectro/604242dd79a0; not r:gt.recipe.multicentrifuge/295ed279224a).

Effort 0 h with parts not counted: option 1 (5 not counted); option 2 (3 not counted); option 3 (5 not counted). The effort term can't tell these options apart (WO-4.3's effort numbers).

143 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-afb1dcb57b) burns Methane in its Turbo Gas Turbine on methane: 399.9 EU/t net, score 0.07744, effort 0 h.
> Option 2 (plan:pw-268393bbe1) burns Hydrogen in its Turbo Gas Turbine on hydrogen: 221.7 EU/t net, score -0.00659, effort 0 h.
> Option 3 (plan:pw-6652815f82) burns Hydrogen in its Turbo Gas Turbine on hydrogen: 219.3 EU/t net, score -0.09659, effort 0 h.

## iv-best: best at IV

Weights: mode best, ranked by the weighted score; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-2af3031bbc | Naquadah Reactor Mk-II on bartworks:gt.bwMetaGeneratedstick@89 | Tiberium Rod (bartworks:gt.bwMetaGeneratedstick@89) | 8,189 | 8,192 | 3.438 | IV x1 | 0.1957 | - | 0 | 3 | 9.437 | no |
| 2 | plan:pw-d541ce8879 | Naquadah Reactor Mk-II on bartworks:gt.bwMetaGeneratedstick@89 | Tiberium Rod (bartworks:gt.bwMetaGeneratedstick@89) | 8,189 | 8,192 | 3.438 | IV x1 | 0.1957 | - | 0 | 3 | 9.437 | no |
| 3 | plan:pw-bdb955bf54 | Turbo Geothermal Engine on lava | Lava (fluid:lava) | 8,192 | 8,192 | 0 | IV x1 | 0.1932 | - | 0 | 1 | 29,491 | no |

Checks:
- options: pass (3 options)
- distinct: pass (no two options the same line)
- order: pass (positions 1..3, by score)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode best, ranked by the weighted score; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

How each option differs from option 1: option 2: 1 recipe(s) option 1 doesn't use; 1 of option 1's recipe(s) not used (uses r:gt.recipe.hammer/3aefd2eb5f79; not r:gt.recipe.hammer/58437ade2af0); option 3: generator Turbo Geothermal Engine on lava; fuel Lava; 2 of option 1's recipe(s) not used.

Effort 0 h with parts not counted: option 1 (3 not counted); option 2 (3 not counted); option 3 (1 not counted). The effort term can't tell these options apart (WO-4.3's effort numbers).

Warnings: r:gt.recipe.multimixer/ef7463e8e51a: every machine for it is deprecated; r:gt.recipe.spaceMining/784e2403a891: every machine for it is custom, flagged, low_confidence

15 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-2af3031bbc) burns Tiberium Rod in its Naquadah Reactor Mk-II on bartworks:gt.bwMetaGeneratedstick@89: 8,189 EU/t net, score 0.1957, effort 0 h.
> Option 2 (plan:pw-d541ce8879) burns Tiberium Rod in its Naquadah Reactor Mk-II on bartworks:gt.bwMetaGeneratedstick@89: 8,189 EU/t net, score 0.1957, effort 0 h.
> Option 3 (plan:pw-bdb955bf54) burns Lava in its Turbo Geothermal Engine on lava: 8,192 EU/t net, score 0.1932, effort 0 h.

## iv-max: max_output at IV

Weights: mode max_output, ranked by net EU/t within the scale bound; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-b305bb9305 | Large Plasma Turbine on plasma.bismuth, Steel large rotor | Bismuth Plasma (fluid:plasma.bismuth) | 8,192 | 8,192 | 0 | IV x1 | 0.1731 | 100,525 | 0.08149 | 16 | 7.2 | no |
| 2 | plan:pw-eff4ff49d6 | Turbo Geothermal Engine on lava | Lava (fluid:lava) | 8,192 | 8,192 | 0 | IV x1 | 0.1932 | - | 0 | 1 | 29,491 | no |
| 3 | plan:pw-0e60beff1f | Turbo Geothermal Engine on gregtech:gt.metaitem.01@2843 | Pyrotheum Dust (gregtech:gt.metaitem.01@2843) | 8,192 | 8,192 | 0.008 | IV x1 | 0.1924 | - | 0 | 2 | 14,636 | no |

Checks:
- options: pass (3 options)
- distinct: pass (no two options the same line)
- order: pass (positions 1..3, by net EU/t)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode max_output, ranked by net EU/t within the scale bound; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

How each option differs from option 1: option 2: generator Turbo Geothermal Engine on lava; fuel Lava; option 3: generator Turbo Geothermal Engine on gregtech:gt.metaitem.01@2843; fuel Pyrotheum Dust; 2 recipe(s) option 1 doesn't use (uses r:gt.recipe.packager/da1c637b20ef, r:gt.recipe.packager/fedc26dd1a31; not ).

Effort 0 h with parts not counted: option 2 (1 not counted); option 3 (2 not counted). The effort term can't tell these options apart (WO-4.3's effort numbers).

Warnings: pin fluid:plasma.bismuth=r:gt.recipe.eyeofharmony/375086cab230 not available on base machines; fluid:plasma.bismuth imported; r:gt.recipe.eyeofharmony/375086cab230: every machine for it is custom, flagged, low_confidence

15 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-b305bb9305) burns Bismuth Plasma in its Large Plasma Turbine on plasma.bismuth, Steel large rotor: 8,192 EU/t net, score 0.1731, effort 0.08149 h.
> Option 2 (plan:pw-eff4ff49d6) burns Lava in its Turbo Geothermal Engine on lava: 8,192 EU/t net, score 0.1932, effort 0 h.
> Option 3 (plan:pw-0e60beff1f) burns Pyrotheum Dust in its Turbo Geothermal Engine on gregtech:gt.metaitem.01@2843: 8,192 EU/t net, score 0.1924, effort 0 h.

## iv-effort: output_per_effort at IV

Weights: mode output_per_effort, ranked by net EU/t per effort hour; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-1181ada154 | Large Plasma Turbine on plasma.bismuth, Steel large rotor | Bismuth Plasma (fluid:plasma.bismuth) | 8,192 | 8,192 | 0 | IV x1 | 0.1736 | 100,525 | 0.08149 | 16 | 7.2 | no |
| 2 | plan:pw-11a321712e | Turbo Semifluid Generator III on glycerol | Glycerol (fluid:glycerol) | 7,071 | 8,192 | 1,121 | IV x1 | 0.1083 | 72,475 | 0.09756 | 13 | 175,029 | no |
| 3 | plan:pw-c7e06c0825 | Turbo Semifluid Generator III on glycerol | Glycerol (fluid:glycerol) | 6,878 | 8,192 | 1,314 | IV x1 | 0.0873 | 70,497 | 0.09756 | 18 | 201,448 | no |

Checks:
- options: pass (3 options)
- distinct: pass (no two options the same line)
- order: pass (positions 1..3, by net EU/t per effort hour (D235/D236 groups first))
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode output_per_effort, ranked by net EU/t per effort hour; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (the validator's verdict on every option)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

How each option differs from option 1: option 2: generator Turbo Semifluid Generator III on glycerol; fuel Glycerol; 4 recipe(s) option 1 doesn't use (uses r:gt.recipe.chemicaldehydrator/5a449f211d5d, r:gt.recipe.fluidextractor/c2b1d96b12a7, r:gt.recipe.fluidextractor/cef0647aa230, r:gt.recipe.largechemicalreactor/7cdb610e5354; not ); option 3: generator Turbo Semifluid Generator III on glycerol; fuel Glycerol; 9 recipe(s) option 1 doesn't use (uses r:gt.recipe.centrifuge/32de467826f7, r:gt.recipe.chemicaldehydrator/5a449f211d5d, r:gt.recipe.fluidextractor/7f373bbe2b10, r:gt.recipe.fluidextractor/c2b1d96b12a7 ...; not ).

Warnings: pin fluid:plasma.bismuth=r:gt.recipe.eyeofharmony/375086cab230 not available on base machines; fluid:plasma.bismuth imported; r:gt.recipe.eyeofharmony/375086cab230: every machine for it is custom, flagged, low_confidence

29 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-1181ada154) burns Bismuth Plasma in its Large Plasma Turbine on plasma.bismuth, Steel large rotor: 8,192 EU/t net, score 0.1736, effort 0.08149 h.
> Option 2 (plan:pw-11a321712e) burns Glycerol in its Turbo Semifluid Generator III on glycerol: 7,071 EU/t net, score 0.1083, effort 0.09756 h.
> Option 3 (plan:pw-c7e06c0825) burns Glycerol in its Turbo Semifluid Generator III on glycerol: 6,878 EU/t net, score 0.0873, effort 0.09756 h.

## iv-sustain: self_sustaining at IV

Weights: mode self_sustaining, ranked by the weighted score, closed lines only; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1

| # | plan | generator | fuel | net EU/t | gross | chain | out | score | EU/t per effort h | effort h | not counted | upkeep /h | closed |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | plan:pw-17798a6787 | Turbo Gas Turbine III on methane | Methane (fluid:methane) | 5,157 | 8,192 | 3,035 | IV x1 | 0.08889 | - | 0 | 5 | 0 | yes |
| 2 | plan:pw-6746cb210a | Turbo Gas Turbine III on hydrogen | Hydrogen (fluid:hydrogen) | 295.7 | 8,192 | 7,896 | IV x1 | -0.09964 | - | 0 | 3 | 0 | yes |
| 3 | plan:pw-4528eaf32c | Basic Gas Turbine on methane | Methane (fluid:methane) | 25.71 | 32 | 6.29 | LV x1 | -0.1101 | - | 0 | 5 | 0 | yes |

Checks:
- options: pass (3 options)
- distinct: pass (no two options the same line)
- order: pass (positions 1..3, by score)
- tool order: pass (design_power's rows in rank order)
- weights: pass (mode self_sustaining, ranked by the weighted score, closed lines only; weights (balanced): net EU/t 1, effort 1, upkeep inputs 1, complexity 1, scarcity 1)
- numbers: pass (net > 0 = gross - chain; cited)
- effort: pass (breakdown on every option; cards pass the catalog)
- closed: pass (every option proven closed, with its kit)
- explain: pass (faithful fake: model; reordering fake: template)
- plan_explain: pass (the deep lane's tool, faithful fake: model)
- view: pass (the Power view's options in the same order)

How each option differs from option 1: option 2: generator Turbo Gas Turbine III on hydrogen; fuel Hydrogen; 1 recipe(s) option 1 doesn't use; 1 of option 1's recipe(s) not used (uses r:gt.recipe.multielectro/599bf7b376e7; not r:gt.recipe.multicentrifuge/295ed279224a); option 3: generator Basic Gas Turbine on methane.

Effort 0 h with parts not counted: option 1 (5 not counted); option 2 (3 not counted); option 3 (5 not counted). The effort term can't tell these options apart (WO-4.3's effort numbers).

178 candidates rejected (see `gos power design`).

Explanation (faithful fake, accepted):

> Option 1 (plan:pw-17798a6787) burns Methane in its Turbo Gas Turbine III on methane: 5,157 EU/t net, score 0.08889, effort 0 h.
> Option 2 (plan:pw-6746cb210a) burns Hydrogen in its Turbo Gas Turbine III on hydrogen: 295.7 EU/t net, score -0.09964, effort 0 h.
> Option 3 (plan:pw-4528eaf32c) burns Methane in its Basic Gas Turbine on methane: 25.71 EU/t net, score -0.1101, effort 0 h.
