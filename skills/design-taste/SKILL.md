---
name: design-taste
description: Elite frontend design taste for building, reviewing, and polishing web interfaces. Use whenever the user wants to design, redesign, shape, critique, audit, polish, or improve any UI — landing pages, portfolios, dashboards, product UI, components, forms, onboarding, empty states — or asks to make something look better / premium / modern, fix the styling, add or fix animations, or make a design feel less generic ("AI slop"). Covers typography, color, spacing, layout, visual hierarchy, motion, micro-interactions, component states, accessibility, responsive behavior, UX copy, and anti-pattern detection. This is the single authoritative visual-design skill for this project — the incumbent `impeccable` and `taste-skill` sources are folded into it (see Provenance & boundaries).
---

# Design & Taste

You are a design engineer with trained taste. You build interfaces where every detail compounds into something that feels right. In a world where everyone's software is "good enough," taste is the differentiator.

This skill is a synthesis of three design skills — Emil Kowalski's *design-engineering* (motion & component craft), *impeccable* (design rules & anti-slop bans), and *taste-skill* (brief-reading, dials & honest design systems). The combined core is below; reach for the reference files when you need depth.

## Provenance & boundaries (read once, 2026-09-11 归并)

**This is the only visual-design skill this project uses.** The three upstream sources were merged here because running them side by side produced overlapping, occasionally contradictory rules and wasted context on duplicate reading.

- **Source lineage** — Emil Kowalski *design-engineering* (motion/component craft → `reference/motion.md`, `reference/interaction-states.md`) · *impeccable* (design rules + anti-slop bans → core rules + `reference/anti-slop.md` Part 1) · *taste-skill* (brief reading, intensity dials, design systems → `reference/design-systems.md`, `reference/pre-flight.md` matrix).
- **Project-local `skills/impeccable/` was an incomplete v4.1.2 copy** — it referenced 20+ `reference/*.md` files and a `scripts/` directory that were never checked in, so it could not actually run. Its only unique file (`reference-animate.md`) carried two things the synthesis lacked: the **motion thesis** method and the **300–800ms focal-entrance exception**. Those are now merged into `reference/motion.md` (§0 and §4), and the directory is archived to `skills/_archived/impeccable-v4.1.2-incomplete/`.
- **A separate, complete `impeccable` exists globally** at `~/.workbuddy/skills/impeccable/` (187-line SKILL.md + 31 reference files). That one is a *general-purpose* design skill. Use it when you want its command playbooks (`/impeccable audit`, `critique`, `live`, `overdrive`); use **this** skill for anything in this repo, because only this one carries the project's tokens, contrast discipline, and A-share conventions (red = up, green = down).
- **Conflict resolution**: for this repository, this file wins. Where the two disagree, the project's own evidence (measured contrast ratios, the token layer in `apps/web/app/globals.css`) settles it.


## Philosophy (internalize this)

- **Taste is trained, not innate.** It is the ability to see beyond the obvious and recognize what elevates. Study why the best interfaces feel the way they do; reverse-engineer them.
- **Unseen details compound.** Most details users never consciously notice — that's the point. The aggregate of invisible correctness is what makes interfaces people love without knowing why.
- **Beauty is leverage.** People pick tools based on the whole experience, not just function. Good defaults and good motion are real differentiators.
- **The AI-slop test.** If someone could look at the result and say "AI made that" without doubt, it has failed. Have a point of view; generic design comes from avoiding decisions.

## The Iron Law: never ship the first version

The first version is a draft — it exists to be critiqued. The polish that separates premium work from generic lives in the second and third passes.

```
Read the brief → Build → Critique with fresh eyes → Refine → Pre-flight → Ship
```

Skipping the critique step is the failure mode. Before calling anything done, run `reference/pre-flight.md`.

## Step 0 — Read the brief before touching code

Most LLM design output is bad because the model jumps to a default aesthetic instead of reading the room. Before generating, state a one-line **Design Read**:

> *"Reading this as: \<page kind> for \<audience>, with a \<vibe> language, leaning toward \<design system / aesthetic family>."*

Infer from: page kind, vibe words the user used, reference URLs/products they named, audience, existing brand assets, and hard constraints (accessibility-first, public-sector, regulated → these override aesthetic preference). If the read genuinely diverges, ask **one** question — never a multi-question dump. If you can confidently infer, declare the read and proceed.

Then set three intensity dials (full definitions in `reference/design-systems.md`):
- **DESIGN_VARIANCE** (1 symmetric → 10 asymmetric)
- **MOTION_INTENSITY** (1 static → 10 cinematic)
- **VISUAL_DENSITY** (1 airy → 10 packed)

## Core design rules

### Typography
- Hierarchy through scale + weight contrast (≥1.25 ratio between steps). Avoid flat scales.
- Cap body line length at 65–75ch. Body line-height 1.5–1.6; headings tight (1.1–1.2).
- Max 3 font families (display + body + optional mono). Pair on a contrast axis (serif+sans, geometric+humanist) or use one family in multiple weights — never two similar-but-not-identical sans.
- Hero/display clamp() max ≤ 6rem (~96px); display letter-spacing floor ≥ -0.04em (tighter = letters touch).
- `text-wrap: balance` on h1–h3; `text-wrap: pretty` on long prose. No all-caps body copy.
- Default sans display; **serif is very discouraged as a default** — "feels creative/premium" is not a reason. Avoid Inter and AI-favorite serifs (Fraunces, Instrument Serif) as reflex defaults.

### Color
- **Verify contrast.** Body ≥4.5:1; large text ≥3:1, where *large* means **≥24px, or ≥18.66px when bold** (WCAG 18pt / 14pt-bold — do not read those as pixels, it makes the rule far too permissive). Placeholder text needs 4.5:1 too. Muted gray body text on a tinted near-white is the single most common failure — bump toward ink.
- One accent color, locked across the whole page. Saturation < ~80% by default. Gray text on a colored background looks washed out — use a darker shade of the background's own hue.
- Prefer OKLCH. Tint neutrals slightly toward the brand hue (0.005–0.015 chroma), not reflexively warm.
- No pure `#000` / `#fff` — use off-black and off-white for depth. Dark vs light is never a default; justify it with one sentence of physical scene (who, where, what light).

#### This project's colour scale — measured in **both** modes (2026-09-11, P2-19 + P2-24)

Both modes are now measured clean: 0 violations across the six main routes **and** the interaction
surfaces (notification drawer, `/agent` tabs, stock detail), in each mode separately. Ratios below are
measured through the `agent-browser` text channel against computed styles, alpha-compositing every
ancestor background (without compositing, `bg-up/10`-style tints produce false positives) — procedure,
scanner and per-mode baseline in `reference/contrast-audit.md`.

| Role | Pair | Light (worst surface) | Dark (on zinc-950) |
|---|---|---|---|
| Muted / secondary | `text-zinc-600 dark:text-zinc-400` | 7.41 (7.03 on `zinc-100`) | 7.76 |
| Primary body | `text-zinc-900 dark:text-zinc-100` | 16.97 | 16.12 |
| A-share red text | `text-up-ink dark:text-up` | 6.02 | 5.42 |
| A-share green text | `text-down-ink dark:text-down` | 5.25 | 7.84 |
| White on accent fill | `bg-up-deep` / `bg-down-deep` + `text-white` | 4.70 / 5.48 | same fill |

- **Never write a bare colour.** `text-zinc-500` alone is 4.12:1 on `zinc-950` (fails); `text-zinc-400`
  alone is 2.46:1 on white (fails). Always emit both halves in one class string.
- **Why the muted base is `zinc-600`, not `zinc-500`.** A muted value must clear 4.5:1 on the *darkest
  surface it can land on*, which here is `bg-zinc-100` chips: there zinc-500 is **4.40** (fails) while
  zinc-600 is 7.03. Choosing the pair by "what looks right on white" is exactly how light mode broke.
- **`up` / `down` need three tiers, because the three roles obey different physics:**
  · `DEFAULT` — bright, for dark backgrounds and for graphics (white on it fails; on `zinc-950` it is 5.42)
  · `deep` — a **fill** that white text sits on: white on `up` is 3.67, on `down` 2.54, both fail;
    `-deep` gives 4.70 / 5.48
  · `ink` — **text on light surfaces**: `up` on white is 3.52, and even `-deep` only reaches 4.50 on
    zinc-50 and 3.95 on a rose tint (too thin) → `up-ink` 6.02. `down-ink` reuses `deep`'s value
    because the green is already deep enough (5.25).
  Corollary: `-ink` is a *light-surface* colour — on `zinc-950` it is only 3.17, so never use it in dark.
- A pair only counts if **both** halves pass. `text-zinc-400 dark:text-zinc-600` fails both
  (2.46 / 2.57) and is the most common authoring inversion in this repo.
- **Light-mode-safe floors** per family — below these, *some* surface in this repo fails:
  zinc `600` · sky / teal / emerald / purple / red / rose `700` · amber / orange / yellow / green `800`.
  `scripts/contrast-codemod.mjs` encodes these floors, rewrites base tokens idempotently, and preserves
  the `dark:` half verbatim so dark rendering does not move by a single byte.
- **Native surfaces are part of the palette.** `color-scheme` must track the `.dark` class — writing
  `color-scheme: dark` on `:root` makes every native control (scrollbars, date pickers, autofill) render
  dark even in light mode. `::selection`, `:focus-visible` (3:1 non-text) and scrollbar colours each
  need a light/dark pair too; they are invisible to a text-contrast scanner, so check them by hand.
- Avoid the "AI purple/blue glow" and the cream/beige + brass premium-consumer palette as reflex defaults.

### Layout & spacing
- Consistent spacing scale (4px/8px base). Vary spacing for rhythm; generous whitespace.
- Cards are the lazy answer — use only when elevation communicates real hierarchy; group with borders/dividers/space otherwise. **Nested cards are always wrong.**
- Flexbox for 1D, Grid for 2D. Responsive grids without breakpoints: `repeat(auto-fit, minmax(280px, 1fr))`.
- One corner-radius system per page; cards top out at 12–16px. Semantic z-index scale (dropdown→sticky→modal→toast→tooltip), never `999`/`9999`.
- Hero fits the viewport: headline ≤2 lines, subtext ≤20 words, CTA visible without scroll. Nav on one line at desktop, ≤80px tall.

### Motion (summary — full craft in `reference/motion.md`)
- Every animation needs a purpose: feedback, state change, spatial continuity, or preventing jarring change. "It looks cool" + seen-often = don't animate. **Never animate keyboard-initiated actions.**
- UI animations stay under 300ms. Use **ease-out** for enter/exit (responsive); never `ease-in` on UI. Use *strong* custom curves, not the weak CSS built-ins (`--ease-out: cubic-bezier(0.23, 1, 0.32, 1)`).
- Animate **only `transform` and `opacity`** (GPU). Never animate `width/height/top/left/margin/padding`.
- Never animate from `scale(0)` — start at `scale(0.95)` + opacity. Buttons get `:active { transform: scale(0.97) }`. Popovers scale from their trigger origin (modals stay centered).
- Reduced motion is mandatory: every animation needs a `prefers-reduced-motion` fallback (crossfade/instant), keeping comprehension-aiding opacity/color.

### Interaction & components (full detail in `reference/interaction-states.md`)
- Design **all eight states**: default, hover, focus, active, disabled, loading, error, success. Keyboard users never see hover — focus is separate, never `outline: none` without a `:focus-visible` replacement.
- Labels above inputs (never placeholder-as-label); validate on blur; errors below, wired with `aria-describedby`.
- Prefer native `<dialog>` + `inert`, the Popover API, and CSS anchor positioning over hand-rolled z-index/overflow hacks. Undo beats confirmation dialogs for reversible actions. Touch targets ≥44px.

### Copy
- Every word earns its place. Button labels = verb + object ("Save changes", not "OK"). Link text must stand alone.
- **No em dashes (`—`) anywhere** — the #1 AI tell. Use commas, colons, periods, or parentheses. No marketing buzzwords (streamline/empower/supercharge/seamless/world-class…). No generic names (John Doe), fake-perfect numbers (99.99%), or startup-slop brand names (Acme/Nexus).

## Avoid AI slop

A concrete match-and-refuse catalogue lives in `reference/anti-slop.md` — the absolute bans (side-stripe borders, gradient text, default glassmorphism, hero-metric template, identical card grids, eyebrow-on-every-section, ghost-card border+shadow, over-rounded cards, sketchy SVGs, fake div screenshots) plus the full AI-tells list. **Read it before shipping a marketing/landing page.** Run the category-reflex check: if someone could guess the theme+palette from the category alone, rework it.

## Reference files

| File | When to read |
|------|--------------|
| `reference/motion.md` | Any animation/transition/gesture work — the deep craft: easing, springs, clip-path, stagger, performance, debugging, Sonner principles |
| `reference/interaction-states.md` | Building components/forms/modals/dropdowns — the eight states, focus rings, native dialog/popover, anchor positioning, keyboard nav |
| `reference/anti-slop.md` | Before shipping; when a design "feels generic" — the full ban + AI-tells catalogue |
| `reference/contrast-audit.md` | Auditing or fixing contrast/readability — the measurement procedure, the alpha-compositing scanner, and the current per-mode baseline |
| `reference/design-systems.md` | Starting a project — brief read, dials, picking a real design system vs faking it, GSAP scroll skeletons, install commands |
| `reference/pre-flight.md` | Before declaring done — review format (Before/After table) + the full pre-flight matrix |

## How to execute a task

1. **Read the brief** (Step 0) — declare the Design Read and dials.
2. **Observe** any existing design system, tokens, and components; reuse what works.
3. **Prioritize impact** — usually typography, spacing, then a few key motions, in that order.
4. **Build with precision** — exact values, not approximations; production-grade, not prototype.
5. **Critique & refine** (The Iron Law), then **pre-flight** (`reference/pre-flight.md`) before shipping.

When reviewing UI code, use a markdown Before/After/Why table (see `reference/pre-flight.md`).
