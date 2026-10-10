# Design System

<!--
The visual system for the dashboard (src/dashboard/frontend/), the docs site (site/docs/) and the landing page (site/web/).
This file uses the DESIGN.md layout impeccable (github.com/pbakaus/impeccable) reads and writes.
Status: the dashboard is about to be redesigned. This file records the brand system that stays and the incumbent dashboard look that is being replaced.
After the redesign ships, run /impeccable document to regenerate it from the real styles.
-->

## Overview

Calm, precise and quiet, in the spirit of Apple's product pages: generous space, one accent, real numbers as the hero, no decoration. Named direction: **signal on black**. The interface recedes so a call's cost and timing read at a glance.

Today two systems coexist. The docs and landing page already use the brand palette in `site/theme.css`. The dashboard uses an older teal system in `src/dashboard/frontend/src/index.css` with Inter and soft shadows. The redesign moves the dashboard onto the brand system and removes the teal one.

## Colors

Brand palette, `site/theme.css` (dark is the default):

| Token | Light | Dark | Use |
| --- | --- | --- | --- |
| `--vg-bg` | #fcfcfc | #0a0a0a | Page background |
| `--vg-surface` | #f2f2f3 | #171717 | Cards, panels |
| `--vg-line` | #e3e3e6 | #262626 | Hairlines and dividers |
| `--vg-line-strong` | #c9c9ce | #444444 | Input borders, emphasis lines |
| `--vg-ink` | #0a0a0a | #fafafa | Headings, numbers |
| `--vg-prose` | #26262b | #cecece | Body text |
| `--vg-muted` | #55555c | #a3a3a3 | Labels, secondary text |
| `--vg-accent` | #6f3fb0 | #cba6f7 | The one signal: primary action, links, active item, focus |
| `--vg-accent-soft` | #e9e3f3 | #1f1a28 | Accent backgrounds |
| `--vg-good` | #4d6b2a | #9ab175 | Success, healthy |
| `--vg-bad` | #a4483b | #d08a7f | Error, failed call |

Incumbent dashboard palette being replaced: teal `#1F96AA` accent on `#F7FAFB`, with green, amber and red status colors.

## Typography

- Docs and landing: Space Grotesk for text, JetBrains Mono for code and figures.
- Dashboard today: Inter for text, JetBrains Mono with tabular numerals (`.mono`) for figures.
- Numbers (cost, latency, units) always use tabular numerals so columns align.
- Hierarchy: display for the one headline number on a page, then headline, title, body and label. Sentence case everywhere.

## Layout

- One primary question per page, answered above the fold by a number.
- Generous whitespace over borders; group by spacing first, lines second.
- Content width capped for reading on the docs; the dashboard uses a fluid grid with a fixed side navigation that collapses on small screens.

## Elevation & Depth

Depth comes from tonal layers (`--vg-bg` under `--vg-surface`) and hairlines, not shadows. The incumbent dashboard uses soft shadows (`--shadow`, `--shadow-hover`); the redesign drops them.

## Shapes

Small, consistent radii. The incumbent dashboard uses 10px for cards, 8px for controls and pill shapes for status badges. Borders are 1px hairlines.

## Components

- **Buttons:** one primary per view in `--vg-accent`; secondary actions are quiet text or outline buttons. Visible focus ring in the accent.
- **Tables:** the core of the dashboard. Right-aligned tabular numbers, muted headers, no zebra stripes, row hover in `--vg-surface`.
- **Charts:** Recharts. Accent for the series that matters, neutrals for the rest; every value also available as text.
- **Status:** `--vg-good` and `--vg-bad` with a label, never color alone. Unknown shows as a dash, never as zero.
- **Navigation:** a short side list of pages; the active item is the only accented one.

## Do's and Don'ts

- Do keep the accent to one element per viewport outside charts. If two accented things compete, one is wrong.
- Do let real data lead: a cost, a latency, a call.
- Do meet 4.5:1 contrast in both themes.
- Don't use em dashes in UI copy, docs or the site.
- Don't add gradients, glass effects, decorative illustrations or a second accent.
- Don't render unknown or unobservable values as 0.
