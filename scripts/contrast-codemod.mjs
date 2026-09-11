#!/usr/bin/env node
// 亮色对比度 codemod（P2-24，2026-09-11）
//
// 目标：让**亮色**渲染达标，同时**深色渲染逐字节不变**。
//
// 安全不变量：
//   1. 只处理「裸」前景色 token（`^text-<fam>-<N>$` / `^text-up$` / `^text-down$`）——
//      带变体前缀的（hover: / dark: / group-hover: / placeholder: …）一律不碰，
//      它们的含义是「某状态下的颜色」，不是该元素的基底色。
//   2. 若同一字面量内**已有** `dark:text-<fam>-…`，只换基底、dark 保持原样；
//      若没有，则替换基底后**原地补 `dark:<原 token>`**，深色渲染因此完全不变。
//   3. 幂等：替换后基底已 ≥ 下限（或已变成 -ink），复跑无变化；补上的 dark: 使第二遍不再补。
//
// 用法：node scripts/contrast-codemod.mjs --dry | --apply
// 验证：改完必须用 agent-browser 双模式实测（见 skills/design-taste/reference/contrast-audit.md），
//       「改了多少行」不能作为达标证据——只能证明改动落盘，不能证明渲染达标。
//
// 两条踩过的坑（都会导致**静默漏改整类**，干跑时务必核对「该改的进候选集了吗」）：
//   · 目录清单必须与 tailwind.config.ts 的 content 完全一致——首轮漏了 hooks/，
//     use-quote-stream.ts 的质量态色表（6 条）整张没进候选集，靠渲染实测才发现。
//   · 模板字面量里 `${cond ? "text-up" : "text-zinc-400"}` 的嵌套字符串，
//     最外层扫描会把它们整段吃掉 ⇒ 需要 processLiteralContent 下钻一层。
import fs from "fs";
import path from "path";
import { fileURLToPath } from "url";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../apps/web");
const DIRS = ["app", "components", "hooks", "lib"]; // 必须与 tailwind.config.ts 的 content 保持一致
const APPLY = process.argv.includes("--apply");

// 亮色安全下限：实测「同色系淡底 + 中性底」中最差背景仍 ≥4.5:1 的最小 Tailwind 档。
// 数值来源见 /tmp 计算 + skills/design-taste/reference/contrast-audit.md。
const FLOOR = {
  zinc: 600, slate: 600, gray: 600, neutral: 600, stone: 600,
  sky: 700, teal: 700, emerald: 700, purple: 700, violet: 700,
  indigo: 700, blue: 700, cyan: 700, pink: 700, fuchsia: 700,
  red: 700, rose: 700,
  amber: 800, yellow: 800, orange: 800, green: 800, lime: 800,
};
// 近白基底（作者显然按深色写、漏了亮色镜像）→ 亮色档。
// 单独一张表是为了**保层级**：zinc-100/200 是「主文本」不是「弱化文本」，
// 直接落到 floor(600) 会把它降级成次要文本。
const MIRROR = { "zinc-100": "zinc-900", "zinc-200": "zinc-900", "zinc-300": "zinc-700" };
// A 股语义色的「亮色文字档」：up/deep 是白字底档，作前景仍不够深，另设 -ink。
const INK = { up: "up-ink", down: "down-ink" };

const CLASSY = /(?:^|\s)(?:[a-z-]+:)*(?:text|bg|border|ring|fill|stroke)-[a-z0-9/[\]().%-]+/;

function lightSafe(fam, n) {
  const mk = fam + "-" + n;
  if (MIRROR[mk]) return "text-" + MIRROR[mk];
  const fl = FLOOR[fam];
  if (!fl || n >= fl) return null;
  return "text-" + fam + "-" + fl;
}

function transformLiteral(s) {
  if (!s.includes("text-") || !CLASSY.test(s)) return null;
  const tokens = s.split(/(\s+)/);
  const hasDarkFam = (fam) => tokens.some((t) => t.startsWith("dark:text-" + fam + "-"));
  const hasDarkUpDown = (name) => tokens.some((t) => t === "dark:" + name || t.startsWith("dark:" + name + "-"));
  let changed = false;

  for (let i = 0; i < tokens.length; i++) {
    const tok = tokens[i];
    const m = /^text-([a-z]+)-(\d{2,3})$/.exec(tok);
    if (m) {
      const fam = m[1];
      const repl = lightSafe(fam, +m[2]);
      if (!repl || repl === tok) continue;
      tokens[i] = hasDarkFam(fam) ? repl : repl + " dark:" + tok;
      changed = true;
      continue;
    }
    const ink = /^text-(up|down)$/.exec(tok);
    if (ink) {
      const name = ink[1];
      tokens[i] = hasDarkUpDown(name) ? "text-" + INK[name] : "text-" + INK[name] + " dark:" + tok;
      changed = true;
    }
  }
  return changed ? tokens.join("") : null;
}

// 模板字面量里 `${cond ? "text-up" : "text-zinc-400"}` 这类**嵌套字符串**，
// 最外层扫描会把它们整段吃掉，导致第一轮漏掉（实测：1639 处降到 61 处后剩下的全是这一类）。
// 所以对反引号字面量要额外下钻一层：先把 ${} 里的字符串字面量各自处理，再处理外层类名段。
function processLiteralContent(kind, content) {
  let out = content;
  let changed = false;
  if (kind === "`") {
    const inner = /(["'])((?:\\.|(?!\1)[^\\])*)\1/g;
    const patches = [];
    let m;
    while ((m = inner.exec(out))) {
      const rep = transformLiteral(m[2]);
      if (rep) patches.push({ s: m.index, e: m.index + m[0].length, r: m[1] + rep + m[1] });
    }
    for (let i = patches.length - 1; i >= 0; i--) {
      out = out.slice(0, patches[i].s) + patches[i].r + out.slice(patches[i].e);
      changed = true;
    }
  }
  const outer = transformLiteral(out);
  if (outer) { out = outer; changed = true; }
  return changed ? out : null;
}

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    if (e.name === "node_modules" || e.name === ".next") continue;
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (/\.(tsx|ts)$/.test(e.name)) out.push(p);
  }
  return out;
}

const LIT = /(["'`])((?:\\.|(?!\1)[^\\])*)\1/g;
const files = DIRS.flatMap((d) => walk(path.join(ROOT, d)));
let nFiles = 0, nLiterals = 0, nTokens = 0;
const samples = [];

for (const f of files) {
  const src = fs.readFileSync(f, "utf8");
  let out = "", last = 0, m, fileChanged = false;
  LIT.lastIndex = 0;
  while ((m = LIT.exec(src))) {
    const content = m[2];
    if (!content.includes("text-")) continue;
    const nb = processLiteralContent(m[1], content);
    if (!nb || nb === content) continue;
    // 统计 token 级改动
    const before = content.split(/\s+/).filter(Boolean);
    const after = nb.split(/\s+/).filter(Boolean);
    nTokens += after.filter((t) => !before.includes(t)).length;
    nLiterals++;
    fileChanged = true;
    if (samples.length < 14) samples.push({ f: path.relative(ROOT, f), before: content.slice(0, 150), after: nb.slice(0, 170) });
    out += src.slice(last, m.index + 1) + nb;
    last = m.index + 1 + content.length;
  }
  if (fileChanged) {
    nFiles++;
    out += src.slice(last);
    if (APPLY) fs.writeFileSync(f, out);
  }
}

console.log((APPLY ? "APPLY" : "DRY  ") + " 文件=" + nFiles + " 类名字面量=" + nLiterals + " 新增 token=" + nTokens);
console.log("--- 样例 ---");
for (const s of samples) {
  console.log("\n[" + s.f + "]");
  console.log("  before: " + s.before);
  console.log("  after : " + s.after);
}
