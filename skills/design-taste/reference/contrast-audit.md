# Contrast audit (measured, not eyeballed)

The rule in `SKILL.md` says "verify contrast". This file is *how* — because the failure mode in this
repo is not ignorance of the standard, it is **guessing at colours instead of measuring them**.

## Why you cannot reason your way to the answer

1. **Semi-transparent backgrounds.** `bg-up/10`, `bg-zinc-500/10`, `backdrop-blur` overlays all mean
   the effective background is a *composite of every ancestor layer*. Reading `backgroundColor` off
   the element itself returns `rgba(...,0.1)` and any naive ratio you compute from it is fiction —
   it produces both false positives (tint read as if it were the full colour) and false negatives.
2. **The rendered value, not the class name.** `bg-up` in one place and `bg-up/10` in another are the
   same string prefix; only `getComputedStyle` tells you what actually painted.
3. **Dark and light are two different designs.** A finding computed in one mode says nothing about the
   other. Always state which mode a measurement came from.

So: measure with the browser, in each mode, after the page has actually rendered.

## Procedure

```bash
# 1. Both dev services must be up (3000 frontend, 8000 backend).
curl -s --noproxy '*' -o /dev/null -w '%{http_code}\n' http://localhost:3000/workbench
curl -s --noproxy '*' -o /dev/null -w '%{http_code}\n' http://localhost:8000/api/health   # 注意是 /api/health，不是 /health（后者 404）

# 2. Scan each route. Re-derive the route list from components/nav-bar.tsx and the app/ tree —
#    do not trust a stale list in a doc.
for p in /workbench /tape /market /picks /research /hunting; do
  agent-browser goto "http://localhost:3000$p" >/dev/null 2>&1
  sleep 3
  printf '%-12s ' "$p"
  cat /tmp/contrast.js | agent-browser eval --stdin
done

# 3. Toggle the theme and repeat — the other mode is a different result set.
agent-browser click "button[aria-label='切换主题']"
```

Surfaces behind interaction (notification drawer, stock detail modal, trade panel, `/agent` tabs) are
**not covered** by a route sweep. Open them first, then scan:

```bash
agent-browser click "button[aria-label='打开通知中心']"
agent-browser goto "http://localhost:3000/agent?tab=alerts"
agent-browser goto "http://localhost:3000/stock/600519"
```

You cannot read PNG screenshots in this environment, so this text channel **is** the observation step.
Do not substitute reasoning for it.

## The scanner

Save as `/tmp/contrast.js` and pipe it in with `agent-browser eval --stdin` (avoids shell quoting
damage on the `${}` in the template strings). It walks every element with a direct text node, skips
script/style/hidden/zero-size nodes, composites the background up the ancestor chain, and applies the
correct threshold for the element's own font size and weight.

```js
(() => {
  function parse(c) {
    const m = c.match(/rgba?\(([\d.]+),\s*([\d.]+),\s*([\d.]+)(?:,\s*([\d.]+))?\)/);
    if (!m) return null;
    return [+m[1], +m[2], +m[3], m[4] === undefined ? 1 : +m[4]];
  }
  function lum(rgb) {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(rgb[0]) + 0.7152 * f(rgb[1]) + 0.0722 * f(rgb[2]);
  }
  function ratio(a, b) {
    const l1 = lum(a), l2 = lum(b);
    return (Math.max(l1, l2) + 0.05) / (Math.min(l1, l2) + 0.05);
  }
  // 逐层向上 alpha 合成：跳过这一层，bg-up/10 这类半透明底会全部变成假阳性
  function bgOf(el) {
    let n = el;
    const stack = [];
    while (n && n !== document.documentElement.parentElement) {
      const c = parse(getComputedStyle(n).backgroundColor);
      if (c && c[3] > 0) stack.push(c);
      n = n.parentElement;
    }
    let base = [255, 255, 255];
    for (let i = stack.length - 1; i >= 0; i--) {
      const c = stack[i], a = c[3];
      base = [c[0] * a + base[0] * (1 - a), c[1] * a + base[1] * (1 - a), c[2] * a + base[2] * (1 - a)];
    }
    return base;
  }
  const out = [];
  document.querySelectorAll("*").forEach((el) => {
    if (el.tagName === "SCRIPT" || el.tagName === "STYLE" || el.tagName === "NOSCRIPT") return;
    const t = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent.trim()).join("");
    if (!t) return;
    const cs = getComputedStyle(el);
    if (cs.display === "none" || cs.visibility === "hidden" || +cs.opacity === 0) return;
    const r = el.getBoundingClientRect();
    if (r.width < 2 || r.height < 2) return;
    const fg = parse(cs.color);
    if (!fg) return;
    const bg = bgOf(el);
    const fgc = fg[3] < 1
      ? [fg[0] * fg[3] + bg[0] * (1 - fg[3]), fg[1] * fg[3] + bg[1] * (1 - fg[3]), fg[2] * fg[3] + bg[2] * (1 - fg[3])]
      : [fg[0], fg[1], fg[2]];
    const cr = ratio(fgc, bg);
    const px = parseFloat(cs.fontSize);
    const bold = +cs.fontWeight >= 700;
    const large = px >= 24 || (px >= 18.66 && bold);   // WCAG 18pt / 14pt-bold，不是 18px/14px
    const need = large ? 3 : 4.5;
    if (cr < need) {
      out.push({
        t: t.slice(0, 34), cr: +cr.toFixed(2), need, px: +px.toFixed(1),
        fg: cs.color, bg: "rgb(" + bg.map((x) => Math.round(x)).join(",") + ")",
        cls: (el.className || "").toString().slice(0, 64),
      });
    }
  });
  return JSON.stringify({ count: out.length, items: out.slice(0, 40) }, null, 1);
})()
```

`items` is capped at 40 but `count` is the true total — judge a page by `count`.

## Reading the result

- **Group by class string, not by element.** A page reporting 60 findings usually comes from 3–4
  distinct class strings rendered many times. Count distinct classes before planning work.
- **Separate true violations from the two benign categories:**
  - *Decorative and exempt*: separators (`·`), purely visual arrows with an `aria-hidden` sibling
    that already carries the meaning. WCAG treats these as incidental. Prefer fixing them anyway so a
    future sweep stays clean, but do not let them drive the priority order.
  - *Scanner artefact*: only ever from skipping the alpha compositing step. If you see a finding whose
    background looks like a 10 % tint of the text colour, suspect the tool before the design.
- **Find the source, not the symptom.** Search the class string in the repo and fix the one definition
  that renders N times.

## Fixing what the scan finds

`scripts/contrast-codemod.mjs` (repo root) rewrites bare foreground tokens to light-safe bases while
**preserving the `dark:` half verbatim**, so dark rendering cannot move:

```bash
node scripts/contrast-codemod.mjs --dry     # 先干跑，核对范围
node scripts/contrast-codemod.mjs --apply
node scripts/contrast-codemod.mjs --dry     # 复跑应为 0 —— 幂等证据
```

It encodes the per-family floors (zinc 600 · sky/teal/emerald/purple/red/rose 700 · amber/orange/
yellow/green 800) and the `up`/`down` → `-ink` mapping. Three traps it was written to survive — all
three cause **silent whole-category misses**, so a dry run must be checked for *"did the things that
should change actually enter the candidate set"*, not for *"how many lines changed"*:

1. **Directory list must equal Tailwind's `content`.** The first pass scanned `app/ components/ lib/`
   and silently skipped `hooks/` — an entire quality-state colour table (6 tokens) never entered the
   candidate set. Only the rendered scan caught it.
2. **Template literals need a nested pass.** `` `${cond ? "text-up" : "text-zinc-400"}` `` — the
   outermost literal match consumes the whole template, so the inner quoted strings are never seen.
   That accounted for every leftover after pass 1 (1639 → 61 → 0).
3. **The `text-` regex must not carry a lookbehind.** Excluding `dark:text-zinc-600` also excludes the
   bases that R2/R3 exist to fix.

## Baseline (2026-09-11, after both sweeps)

| Mode | `/workbench` | `/tape` | `/market` | `/picks` | `/research` | `/hunting` | `/agent` ×4 | `/stock/600519` | `/hunting?tag=intraday` | notification drawer |
|---|---|---|---|---|---|---|---|---|---|---|
| dark (default) | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| light | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |

Before the light sweep the same rows read 137 / 345 / 110 / 479 / 81 / 479 — the root cause was not
scattered typos but **two opposite authoring habits coexisting**: one set of components written for
light and missing its `dark:` half, another written for dark and using the dark value as the base
(`text-zinc-200 dark:text-zinc-100` rendered 1.22:1 on white — effectively invisible). Any given
component was therefore correct in exactly one mode.

**Residual, not covered by this scanner** (text-contrast only, so still worth a manual look):
`::selection`, `:focus-visible`, scrollbar colours, `color-scheme`, and **canvas-drawn colours** —
the K-line MA lines (`#facc15` is 1.47:1 on a light card) are still bright-on-light, because the
chart builds its series once and does not rebuild on a theme toggle. Tracked separately in
`docs/retro-and-gaps.md`.

### The scan only sees what is on screen

A clean route means *that render* is clean, not that the component is. Anything behind a click —
a collapsed panel, a modal that is not open, an empty state, a hover-only affordance — is invisible
to it. Confirmed instance (2026-09-11): the assistant's quick-ask panel only renders its suggestion
rows when `messages.length === 0`, and the arrow glyph in those rows carried `dark:text-zinc-600`
(2.59:1 on `zinc-950`). It was found by a **static complement**, not by the scan — so run this grep
after any sweep, and keep it in the definition of done:

```bash
rg 'dark:text-(zinc|slate|gray|neutral|stone)-(500|600|700|800|900)' apps/web   # 深色侧必然失败
```

Opening every collapsible surface is the manual half; the grep is the cheap half that does not
depend on remembering to click.

### It is a *text* scanner, so icons are invisible to it

Icon-only controls have no text node, so nothing is measured. Confirmed instance (2026-09-11): the
floating assistant orb (`[data-testid="assistant-ball"]`) is `bg-zinc-900 text-zinc-600` in light
mode — the mark is an SVG on `stroke="currentColor"`, and **2.31:1** against its own background, below
the 3:1 that WCAG 1.4.11 asks of a graphical object. The identical pair is reused for the panel's
header badge. The scanner reported 0 on every route the whole time.

Notice how it got in: the same token is *fine* as text elsewhere, because the failure is not in the
colour but in the **pairing** — `text-zinc-600` is compliant on a light surface and non-compliant on
`bg-zinc-900`. Any rule of the form "this token is safe" is only ever true relative to a background.
For icon-bearing elements, read the computed `background-color` and `color` of the host with a DOM
probe (`agent-browser eval`, `getComputedStyle`) in both modes — there is no static shortcut.

Do not report "contrast is fixed" from a single-mode scan. Say which mode you measured.
