<!-- SEED: established with the user before implementation; re-run /impeccable document once there's code to capture the actual tokens and components. -->
---
name: VoiceGateway
description: A self-hosted profiler for LiveKit voice agents. One call, one quiet view.
---

# Design System: VoiceGateway

<!--
Scope: the console in dashboard/ and the docs in site/docs. The landing page (site/) still uses the older purple system in site/theme.css until it is moved onto this one.
Contrast ratios below were measured, not estimated.
-->

## Overview

**Creative North Star: "The Clean Room"**

The console is a clean room for looking at one call: bright, still and uncluttered, so the evidence is the only thing in view. It follows Apple's product language. Light by default, large tight headlines, soft grey grouping under white cards, one accent, and nothing that decorates. Premium comes from restraint and precision, not from effects.

It is a working tool, not a landing page. Pages are calm and spacious, while tables and the call timeline stay dense enough to read a whole call without scrolling sideways. Every screen is composed from pre-built components (shadcn/ui, Magic UI and other shadcn registries), themed with the tokens in this file. Nothing visual is hand-built when a library component exists.

**Key Characteristics:**
- Light first, with a dark mode designed on its own terms, not inverted.
- One accent: Apple blue, rare on purpose.
- Depth from tonal layers (white on soft grey), not shadows.
- Large radii, hairline separators, generous space around dense data.
- Real numbers are the hero; unknown values are shown as unknown.

## Colors

A near-monochrome Apple palette with one blue signal.

### Primary
- **Apple Blue** (#0071E3 light): primary buttons (white label, 4.7:1), links, the active nav item, focus rings, the one chart series that matters. 4.7:1 on white, 4.3:1 on Grouped Grey, so text-sized blue sits on white surfaces only.
- **Apple Blue, dark** (#2997FF dark): the same roles in dark mode (7.0:1 on black, 5.6:1 on grouped grey, black label on it 7.0:1).
- **Blue Mist** (#e8f1fc light, #0b2340 dark): accent backgrounds: selected rows, active filter chips.
- **Gateway Teal** (#1F96AA): the logo only. Never a UI color.

### Neutral
- **Canvas** (#ffffff light, #000000 dark): page background and cards.
- **Grouped Grey** (#f5f5f7 light, #1c1c1e dark): grouping behind cards, the sidebar, alternating sections.
- **Raised** (#ffffff light, #2c2c2e dark): cards that sit on Grouped Grey, popovers, menus.
- **Separator** (#d2d2d7 light, #38383a dark): hairlines and input borders.
- **Ink** (#1d1d1f light, #f5f5f7 dark): headlines, numbers, primary text (16.8:1 and 19.3:1).
- **Secondary Ink** (#6e6e73 light, #a1a1a6 dark): labels, the second tone of a headline, secondary text (5.1:1 on white, 6.6:1 on dark grouped).

### Status
- **Healthy** (#1e7b34 light, #30d158 dark): success, healthy node.
- **Failed** (#d70015 light, #ff453a dark): error, failed call, dropped leg.
- Status is always a label plus color, never color alone.

### Named Rules
**The One Signal Rule.** Blue appears on at most one interactive element per viewport outside charts. If two blue things compete, one is wrong.
**The White Ground Rule.** Text-sized blue sits on white (4.7:1), never on Grouped Grey (4.3:1).

## Typography

**Display and Body Font:** the system stack (`-apple-system, BlinkMacSystemFont, "SF Pro Text", "Segoe UI", Inter, sans-serif`), which renders as SF Pro on Apple devices.
**Mono Font:** `"SF Mono", ui-monospace, "JetBrains Mono", monospace`, for ids, span names and code only. Never as a costume for numbers.
**Wordmark:** Mergepoint and Inter Semibold lettering form one outlined SVG, in light and dark variants. The 16px lettering uses 600 weight, −0.2px tracking and an 8px visible gap from the 28px symbol. Inter follows the neutral shape of the system UI. The logo alone has a brief teal electric sweep on entry and hover; it stays static with reduced motion.

**Character:** Apple's own voice: confident, tight headlines over plain, comfortable text.

### Hierarchy
- **Display:** the one headline number or statement on a page. Tight tracking, semibold. [exact sizes to be resolved during implementation]
- **Headline:** page titles, often two-tone: Ink then Secondary Ink.
- **Title:** card and section titles.
- **Body:** text and table cells, max 70ch for prose.
- **Label:** column headers, metadata. Sentence case, Secondary Ink.

### Named Rules
**The Two-Tone Headline Rule.** A page headline may pair an Ink statement with a Secondary Ink follow-up, Apple style. Never more than two tones.
**The Tabular Rule.** Every number that can sit in a column uses the system sans with tabular figures, so columns align without a monospace font.

## Layout

A fixed left sidebar on Grouped Grey, content on Canvas. Pages answer one question above the fold, usually with a number. Space is generous between groups and tight within them: a call timeline or table stays compact inside a roomy page. The sidebar collapses to a top bar on narrow screens. [grid, breakpoints and spacing scale to be resolved during implementation]

## Elevation & Depth

Flat by default. Depth comes from tonal layering: white Raised cards on Grouped Grey. No shadows at rest; a faint shadow is allowed only on floating layers (menus, popovers, dialogs) so they read as above the page.

**The Flat Rule.** If a card needs a shadow to stand out, the grouping is wrong.

## Shapes

Soft, generous corners in the Apple manner: large radius on cards and panels (about 16 to 20px), smaller on controls, pill shapes for status chips and segmented controls. Borders are 1px hairlines in Separator, used only where tonal layering cannot separate. [exact radii to be resolved during implementation]

## Components

The console is assembled from libraries, themed only through the tokens above.

- **Primitives:** shadcn/ui (Radix base): sidebar, table, card, input, toggle group, dropdown menu, avatar, breadcrumb, tooltip, empty state.
- **Charts and data viz:** Tremor's copy-paste components (Apache-2.0, Tailwind v4), recoloured through an `accent` and a `quiet` entry in their chart colours. Bar charts, bar lists, trackers and category bars. The span timeline stays on shadcn Chart range bars.
- **Gauges:** Gauge UI (@gauge-ui registry), for infrastructure limits.
- **Icons:** Phosphor, regular weight, 18px, one family everywhere. No Lucide in our own code.
- **Containers:** one bordered panel per block (white, 1px hairline, 12px radius), never nested. Measurements sit in one divided stat strip, not a grid of hero cards.
- **Status:** a dot plus a word, never color alone.

## Do's and Don'ts

### Do:
- **Do** compose every screen from pre-built components (shadcn/ui, Magic UI and other shadcn registries) themed with these tokens.
- **Do** keep text contrast at 4.5:1 or above in both themes; the ratios above are the reference.
- **Do** show unknown and unobserved values as a dash with a reason on hover, never as 0.
- **Do** let real data lead each page: a latency, a call, a node.
- **Do** design dark mode as its own world (black canvas, grouped greys, #2997FF accent).

### Don't:
- **Don't** hand-write component markup, CSS or SVG when a library component exists.
- **Don't** use the logo teal (#1F96AA) anywhere in the interface.
- **Don't** add gradients, glass effects, heavy shadows, decorative illustration or a second accent.
- **Don't** use em dashes in UI copy.
- **Don't** reuse the old teal dashboard look (teal on #F7FAFB with soft shadows) or the purple site palette in the console.
