# Age themes (D81)

The dashboard's look follows Clayton's voltage age. Each age has its own theme, and two comfort modes can replace it at any time.

**Design source:** the Claude Design canvas (D79), rows "Ages 1 to 5", "Ages 6 to 10" and "Comfort modes". Each board there is the Now view in that theme with sample data. The sample content follows what is live at that age (§7.9): saves only before MV, OC from MV, AE2 crafts from HV/EV.

## What changes and what doesn't

- **Changes per theme:** colour tokens, type, panel frames, background texture, and the core's artwork (glance core and voice-strip orb).
- **Never changes:** layout, components and shortcuts. Switching themes never moves anything (§7.14 rule 1).
- **Kept in every theme (§7.14 rule 3):**
  - GT `TIER_COLORS` for tier chips, always shown with the tier name.
  - Magenta only for "needs you".
  - Status colours only for status, always with a glyph.
  - Tabular figures.
  - The motion budget (rule 6).
- **Contrast:** in every theme, body text is at least 4.5:1 and large text at least 3:1 against its own panel.

## The ages

| Age | Detected tier | Theme | Look | Core art |
|---|---|---|---|---|
| 1 | Stone, Steam | Brass and Steam | Brass plates with rivets, typewriter labels, serif titles | Pressure gauge; the needle shows the voice state |
| 2 | LV | Machine GUI | GT's own GUI: grey bevelled panels, inventory slots, pixel headings, tier names in tooltip chips | Pixel orb in LV green |
| 3 | MV | Brushed Aluminium | Machined aluminium panels, steel screws, engraved labels, anodized gold | Machined dial with a gold ring |
| 4 | HV | Command HUD | The v0.4 look: dark glass, corner brackets, mono labels | Particle sphere in HV yellow |
| 5 | EV | Circuit Board | Solder-mask green, gold traces, panels drawn as chips with pins, SMD-LED status | Chip with a die in EV grey |
| 6 | IV | Tungstensteel | Gunmetal plate, bolt heads, condensed industrial type, IV-blue light, red hazard striping on alerts | Hex hatch with a blue viewport |
| 7 | LuV | Hologram Lab | Glass panels, cyan light, scanlines | Wireframe hologram |
| 8 | ZPM | Fusion | Containment glass, plasma glow in ZPM aqua | Tokamak ring |
| 9 | UV | Naquadah | Black glass, deep green energy, headings underlined like GT's UV+ tier names | Naquadah crystal |
| 10 | UHV and up | Deep Space | Star field, starlight panels | Accretion disk |

**Comfort modes**, which he can pick at any age:
- **Calm Ops**, for long sessions: bigger type, plain labels, no ornaments.
- **Field Manual**, for daylight: dark ink on paper, with warnings striped as well as coloured.

**Colour clashes, handled per theme:**
- LuV's tier pink is close to "needs you". In Hologram Lab, LuV pink appears only in tier chips, and approval cards add a double border and a "!" badge.
- UHV's dark red is close to error red. In Deep Space, UHV red appears only in tier chips, and errors keep their glyph.

## Switching

- The theme follows tier detection (D64, WO-1.5), including Clayton's override. The Stone and Steam ages use age 1 until LV is detected.
- **Tier-up moment:** the new theme fades in once over 1.5 s, unless "Pause motion" is on. GOS also writes a short recap of the age just finished to the journal: hours played, machines built and best net power.
- **Pinning:** he can pin any age he has reached. Ages beyond his tier stay hidden, like quest spoilers (rule 5), until he asks to see them.
- **Comfort modes** replace the age theme until he switches back.

## Build notes

- **WO-2.0:** the Design System artifact gets one token set per theme and per comfort mode, plus the core art at glance size and voice-strip size.
- **WO-2.6:** theme switching from the detected tier, pinning, the comfort modes and the tier-up moment.
- **Acceptance for WO-2.6:**
  - Switching themes never changes the layout.
  - Every theme passes the contrast rule.
  - Approvals and status stay distinguishable in all twelve themes.
- **Fonts:** all are Google Fonts under the OFL. Bundle them, with their licence files, in the dashboard rather than loading them from Google at runtime.
- **Ship order:** day one needs ages 1–3 and both comfort modes, because the new world starts in the Stone Age. Ages 4–10 can land after day one, each before he reaches it.
- **Effort:** about 1–2 h more of Clayton's time in P2, reviewing each theme on the real screen.
