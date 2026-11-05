# Gregtec OS

One HUD that changes its look at every voltage age.

Gregtec OS (GOS) is a voice assistant and planning suite for GT New Horizons 2.9. Its dashboard runs on a second monitor beside the game. This system is the WO-2.0 design system (D79): the tokens, type, spacing and core components the dashboard is built from. The views themselves are the boards on the Claude Design canvas "Gregtec OS dashboard mockups", approved on 2026-11-05.

## Content fundamentals

GOS talks like a calm shift lead standing next to the player: short, plain and specific.

- Lead with what matters, then the number: "HV is short on power. −380 EU/t."
- Every number comes from the planner or the game, never an estimate, and carries a citation chip (`r:`, `plan:`, `q:`, `crop:`, `w:`). A "data stale" badge replaces it when the fingerprint doesn't match.
- Numbers carry a sign or a unit: −380 EU/t, +210, 41/64, LSC 38%. Use tabular figures.
- Live numbers name their source and age: "OC · 4 s", "Save · 2 min ago".
- Approvals read back exactly what will happen, in quotes, and end with "Or just say confirm".
- Headings are plain: "Now", "Next step", "Waiting for you", "Base · stalled first". No exclamation marks, no jokes in alerts.
- Labels are capitals with wide tracking in most themes and sentence case in Calm Ops and Machine GUI.

## Visual foundations

### Rules every theme keeps

- **One component catalog.** Agents return structured cards (recipe card, route comparison, BOM table, flow diagram, quest card, ore list, power ledger, approval card, alert, health), never HTML.
- **Tier colours are GT's own** `TIER_COLORS` (the `tier-*` tokens), always shown with the tier name. UV and up are underlined, as in GT.
- **Magenta means "needs you"** (`needs-you` and each theme's `<slug>-needs`) and nothing else.
- **Status colours mean status** (`<slug>-ok`, `-warn`, `-err`) and always come with a glyph and a word.
- **Motion only on a state change.** A theme change fades in over 1.5 s, glance mode is capped at 30 fps, and "Pause motion" turns motion off.
- **Layout never changes with the theme.** Only tokens, frames, textures and the core art do (D81).

### Themes: one per voltage age (D81)

The theme follows the player's tier, which GOS reads from quest progress (D64). A tier-up re-themes once (1.5 s) and writes an era recap to the journal. Ages beyond the player's tier stay hidden like spoilers, and any reached age can be pinned. Calm Ops and Field Manual are comfort modes, available at any age.

| Age | Theme | Slug | Display / body / mono faces | Frame and texture | Core art |
|---|---|---|---|---|---|
| 1 · Stone and Steam | Brass and Steam | `brass` | Cinzel / Source Sans 3 / Courier Prime | Brass frame with an inner gold line and rivets in the corners; plate seams in the background | Pressure gauge; the needle shows the voice state |
| 2 · LV | Machine GUI | `machine` | Pixelify Sans / Inter / Pixelify Sans | Minecraft container bevels (white top-left, dark bottom-right, black outline), inset slots, MC buttons, tooltip chips, a dark checker background | Pixel orb in the tier colour |
| 3 · MV | Brushed Aluminium | `alu` | Barlow Semi Condensed / Barlow / Roboto Mono | Brushed grain on light metal panels, steel screws in the corners, engraved labels; graphite shell | Machined dial with a gold ring |
| 4 · HV | Command HUD | `hud` | Saira Condensed / Saira / JetBrains Mono | Corner brackets on panels, a perspective grid floor, a soft glow in the tier colour | Particle sphere in the tier colour |
| 5 · EV | Circuit Board | `circuit` | Chakra Petch / Chakra Petch / Share Tech Mono | Panels drawn as chips with silver pins, gold trace rules ending in vias, PCB traces in the background, SMD-LED status lights | Chip with a die in the tier colour |
| 6 · IV | Tungstensteel | `tung` | Barlow Condensed / Barlow / Barlow Condensed | Gunmetal plates with bolt heads, red hazard striping on alerts, a fine diagonal texture | Hex hatch with a blue viewport |
| 7 · LuV | Hologram Lab | `holo` | Orbitron / Exo 2 / Space Mono | Glass panels with cyan corner ticks and glow, scanlines in the background. LuV pink only in tier chips; approval cards get a double border and a "!" badge | Wireframe hologram on a projector beam |
| 8 · ZPM | Fusion | `fusion` | Rajdhani / Titillium Web / Rajdhani | Containment-glass panels with an aqua top edge, plasma glows in the background | Tokamak ring with plasma |
| 9 · UV | Naquadah | `naq` | Michroma / Sora / Martian Mono | Black glass panels with a green inner glow, a hex lattice in the background, headings underlined like GT names its UV+ tiers | Naquadah crystal on a glowing underline |
| 10 · UHV and up | Deep Space | `deep` | Unbounded / Manrope / DM Mono | A star field and nebula behind translucent panels, pill controls. UHV red only in tier chips so red still means an error | Accretion disk |
| Comfort mode | Calm Ops | `calm` | Inter / Inter / JetBrains Mono | None: no ornaments, rounded cards, sentence-case labels, bigger type | Concentric rings in the tier colour |
| Comfort mode | Field Manual | `manual` | IBM Plex Serif / IBM Plex Sans / IBM Plex Mono | Graph paper, ink rules under headings, views as binder tabs, deficits striped as well as red | Printed seal with the tier |

### Using the tokens

A design system here holds at most 8 colour themes and Gregtec OS has 12, so each theme's colours are their own tokens, named `<slug>-<role>` (`brass-panel`, `holo-needs`). `components/bundle.css` maps them onto one set of semantic names per theme:

```css
.theme-brass {
  --bg: var(--brass-bg); --panel: var(--brass-panel); --text: var(--brass-text); /* ...every role */
  --font-display: 'Cinzel', sans-serif; --radius-panel: var(--brass-radius-panel);
}
```

Components read only the semantic names (`var(--panel)`), so switching theme is one class or `data-theme` change on the root. No class means the Command HUD.

### Colour roles

| Role | Use |
|---|---|
| `bg` | Page background behind panels. |
| `panel` | Card and panel surface. |
| `border` | Panel and control borders. |
| `rule` | Hairlines and row dividers. |
| `text` | Primary text on a panel. |
| `text-2` | Secondary text and labels on a panel. |
| `text-3` | Disabled controls and decoration only. Below 4.5:1 in some themes, so anything the player needs to read uses text-2. |
| `accent` | Theme accent: current view, focus and highlights. |
| `primary` | Primary button fill. |
| `primary-ink` | Text on a primary button. |
| `track` | Bar and meter track. |
| `ok` | Status OK. Always shown with a glyph. |
| `warn` | Status warning. Always shown with a glyph. |
| `err` | Status error. Always shown with a glyph. |
| `needs` | "Needs you" heading and accents. Used for nothing else. |
| `needs-border` | Approval card border. |
| `needs-tint` | Approval card fill. |
| `confirm` | Confirm button on an approval card. |
| `confirm-ink` | Text on the confirm button. |
| `shell-bg` | Nav, rail and voice strip background. |
| `shell-text` | Text in the nav, rail and voice strip. |
| `shell-text-2` | Secondary text in the nav, rail and voice strip. |

Theme-only extras (bevels, rivets, pins, hazard stripes, glows) are tokens too, such as `machine-bevel-light` and `tung-hazard`. They belong to the theme's frame and never carry meaning.

### Contrast

WCAG ratios, each against the surface it sits on: text on the panel, `shell-text-2` and `needs` on the shell, button text on its fill. Translucent panels are composited over the page background first.

| Theme | text | text-2 | text-3 | shell text-2 | needs | primary | confirm | err |
|---|---|---|---|---|---|---|---|---|
| Brass and Steam | 13.4 | 8.3 | 3.2 (large text only) | 9.0 | 7.1 | 10.0 | 6.5 | 6.2 |
| Machine GUI | 6.2 | 4.9 | 2.5 (fails) | 7.1 | 4.7 | 4.8 | 5.6 | 4.8 |
| Brushed Aluminium | 10.0 | 5.0 | 2.4 (fails) | 7.5 | 5.5 | 7.8 | 6.5 | 4.7 |
| Command HUD | 15.9 | 8.5 | 5.9 | 8.7 | 6.6 | 16.6 | 6.5 | 6.9 |
| Circuit Board | 15.8 | 9.3 | 4.0 (large text only) | 8.9 | 5.8 | 8.5 | 6.5 | 6.5 |
| Tungstensteel | 12.4 | 6.0 | 2.7 (fails) | 7.0 | 5.9 | 5.7 | 6.5 | 5.5 |
| Hologram Lab | 17.4 | 10.7 | 4.2 (large text only) | 11.0 | 6.6 | 13.1 | 6.5 | 6.8 |
| Fusion | 17.4 | 8.8 | 3.2 (large text only) | 9.4 | 6.6 | 15.5 | 6.5 | 6.7 |
| Naquadah | 17.5 | 8.8 | 3.5 (large text only) | 9.0 | 6.7 | 14.6 | 6.5 | 7.1 |
| Deep Space | 16.7 | 8.1 | 2.9 (fails) | 8.3 | 6.7 | 16.6 | 6.5 | 7.0 |
| Calm Ops | 15.0 | 7.1 | 4.8 | 7.8 | 6.4 | 16.2 | 6.5 | 7.0 |
| Field Manual | 15.9 | 6.6 | 4.4 (large text only) | 5.6 | 5.3 | 14.6 | 6.6 | 6.2 |

Every pair except `text-3` meets 4.5:1 in all 12 themes (the lowest is 4.7, Brushed Aluminium `err`). `text-3` runs from 2.4 to 5.9:1, so it is for disabled controls and decoration only. A dim value that still carries information uses `text-2`.

### Tier colours

| Tier | Token | Value | Note |
|---|---|---|---|
| Steam | `tier-steam` | #B5893B | GOS's own colour for the Stone and Steam ages; GT has no tier colour here. |
| ULV | `tier-ulv` | #FF5555 | GT red. |
| LV | `tier-lv` | #00AA00 | GT dark green. |
| MV | `tier-mv` | #FFAA00 | GT gold. |
| HV | `tier-hv` | #FFFF55 | GT yellow. |
| EV | `tier-ev` | #555555 | GT dark grey. Needs the edge token on dark grounds. |
| IV | `tier-iv` | #5555FF | GT blue. |
| LuV | `tier-luv` | #FF55FF | GT light purple. Close to "needs you": tier chips only. |
| ZPM | `tier-zpm` | #55FFFF | GT aqua. |
| UV | `tier-uv` | #00AA00 | GT dark green, underlined. |
| UHV | `tier-uhv` | #AA0000 | GT dark red, underlined. Close to error red: tier chips only. |
| UEV | `tier-uev` | #AA00AA | GT dark purple, underlined. |
| UIV | `tier-uiv` | #0000AA | GT dark blue, bold underline. Needs the edge token on dark grounds. |

Text on a tier chip is `tier-ink-dark` on the light tiers and `tier-ink-light` on the dark ones. Dark tiers get the `tier-edge` hairline on dark grounds.

### Type

Three roles per theme: **display** for titles, big numbers and the clock; **body** for sentences and the nav; **mono** for labels, chips and sources. The scale here is the Command HUD's (`type-big-number` 110px down to `type-chip` 13px). Other themes swap the faces at the same roles (table above) and keep the sizes, except Calm Ops, which runs about 10% larger, and glance mode, which uses `type-glance-body` (22px) for anything read from across the desk. Every face is a Google Font under the OFL. The app bundles them with their licence files and never loads them from Google at runtime; only these previews do.

### Spacing and layout

Spacing runs `space-1` (4px) to `space-9` (48px): `space-5`/`space-6` pad panels, `space-3` separates controls and `space-9` pads glance mode. Radii come per theme (`<slug>-radius-panel`, `<slug>-radius-control`), from square in the HUD, Machine GUI and Hologram Lab to pills in Deep Space.

Monitor 2 is 2560×1440 (`board-width`, `board-height`; D50): a header (wordmark, search, status, clock), the nav (`nav-width`), the view, the right rail (`rail-width`: agent, approvals, jobs) and the voice strip. Glance mode drops the nav and rail. The phone layout is 390px wide (`phone-width`; D53). Every click target is at least `hit-min`.

## Iconography

GOS has no icon set. State is a text glyph in the status colour, always next to a word:

| Glyph | Meaning |
|---|---|
| ▲ | Warning or error |
| ● | OK, live, listening |
| ✓ | Done |
| ◐ | In progress |
| ○ | To do |
| ◼ | Paused or stopping |
| ✕ | Failed or missing |
| ◌ | Waiting for something outside GOS |

The other marks are the tier chip (the most common "icon"), number keys in the nav, and each theme's core art (the Core panel on Now and the orb in the voice strip), which shows the voice state (listening, thinking, speaking). Core art is drawn shapes in the theme and tier colours: no pictures, mascots or game textures.

## Components

Panel, AlertCard, ApprovalCard, TierChip, PowerRow, StatusGlyph, CitationChip, Button and NavItem are shown here in several themes each. The full views and the rest of the catalog (route comparison, BOM table, flow diagram, quest card, ore list, health, recipe card) are on the canvas boards, which Claude Code receives through the Claude Design handoff at WO-2.6.
