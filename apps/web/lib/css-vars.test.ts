import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { lineOf, maskComments } from "@/lib/src-mask";

/**
 * CSS 自定义属性「引用必须有定义」守卫（2026-09-14 审查批次 B5）。
 *
 * ## 被守的是什么
 * `var(--x)` 在 `--x` 未定义时**不会报错**：浏览器静默采用 fallback（无 fallback
 * 则整条声明失效）。TypeScript、ESLint、`next build`、单测**全部看不见**——
 * 这类缺陷只在「看图」时才暴露，属最典型的静默失效。
 *
 * ## 真实事故（本轮修复）
 * `hunting/intraday-sections.tsx` 用 `var(--color-up, #16a34a)` /
 * `var(--color-down, #dc2626)` 绘制「发酵 / 证伪」堆叠柱，而这两个变量
 * **全仓从未定义**（`globals.css` 定义的是 `--accent` / `--accent-down`）⇒
 * fallback 必然生效：正向的「发酵」被画成**绿**、负向的「证伪」被画成**红**，
 * 与 A 股红涨绿跌及 `tailwind.config` 的 up/down 定义**完全相反**。
 *
 * 同批还核查了 `stock-detail.tsx` 的 `var(--right-w)`：它由
 * `style={{ "--right-w": `${rightW}px` }}` 在**运行时注入**，属误报——
 * 因此本守卫必须把「字符串字面量形式的内联注入」也算作定义来源，否则会假红。
 *
 * ## 口径（定义来源两类，缺一不可）
 *  ① `.css` 里的声明 `--x: …`；
 *  ② 源码里作为**字符串字面量**出现的 `"--x"`（React 注入 CSS 变量的唯一写法）。
 * 引用面 = 源码骨架里的 `var(--x)`；**有 fallback 不算有定义**（这正是事故形态）。
 *
 * ## 为什么必须掩注释
 * 修复代码的注释必然要**提到**出问题的变量名（本文件上面那段就在提）。
 * 不掩掉就会把说明文字当成引用 ⇒ 假红，而假红的信息会引导后人删掉正确的
 * 说明（KB-ENG-66：源码级守卫必须区分「调用」与「提到调用的文字」）。
 */

const WEB_ROOT = path.resolve(import.meta.dirname, "..");
const SKIP_DIRS = new Set(["node_modules", ".next", "out", "dist", "coverage", ".turbo"]);
/** 只有这三类文件会出现 CSS 变量：样式表与源码。刻意扫全量再过滤（KB-ENG-60）。 */
const SCAN_EXT = new Set([".ts", ".tsx", ".css"]);

function isProductionSource(rel: string): boolean {
  if (/\.(test|spec)\.(m|c)?[jt]sx?$/.test(rel)) return false;
  return !rel.split(path.sep).includes("__tests__");
}

function* walk(dir: string, prefix = ""): Generator<string> {
  for (const entry of readdirSync(dir)) {
    const rel = prefix ? `${prefix}/${entry}` : entry;
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) {
      if (SKIP_DIRS.has(entry)) continue;
      yield* walk(full, rel);
    } else if (SCAN_EXT.has(path.extname(entry))) {
      yield rel;
    }
  }
}

/** 引用：`var(--x)` 与 `var(--x, fallback)` 一律算引用——**有 fallback 不等于有定义**。 */
const REF = /var\(\s*(--[A-Za-z0-9_-]+)/g;
/** 定义①：样式表声明 `--x:`。前置分隔符限制为行首/`;`/`{`/空白，避免匹配到 `a--b: `。 */
const CSS_DEF = /(?:^|[;{\s])(--[A-Za-z0-9_-]+)\s*:/gm;
/** 定义②：字符串字面量形式的变量名（React 内联注入 `"--right-w": …`）。 */
const LITERAL_DEF = /["'`](--[A-Za-z0-9_-]+)["'`]\s*:/g;

type Scan = {
  files: number;
  refs: string[];
  refNames: Set<string>;
  defs: Set<string>;
};

function scan(): Scan {
  const refs: string[] = [];
  const refNames = new Set<string>();
  const defs = new Set<string>();
  let files = 0;

  for (const rel of walk(WEB_ROOT)) {
    if (!isProductionSource(rel)) continue;
    files++;
    const masked = maskComments(readFileSync(path.join(WEB_ROOT, rel), "utf8"));

    REF.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = REF.exec(masked)) !== null) {
      refNames.add(m[1]);
      refs.push(`${rel}:${lineOf(masked, m.index)} → ${m[1]}`);
    }

    for (const re of [CSS_DEF, LITERAL_DEF]) {
      re.lastIndex = 0;
      while ((m = re.exec(masked)) !== null) defs.add(m[1]);
    }
  }

  return { files, refs: refs.sort(), refNames, defs };
}

describe("CSS 自定义属性：引用的变量必须有定义", () => {
  it("生产源码里没有引用未定义的 CSS 变量（否则静默走 fallback）", () => {
    const { refNames, defs } = scan();
    const dead = [...refNames].filter((name) => !defs.has(name)).sort();
    expect(
      dead,
      `以下 CSS 变量被 var() 引用但全仓从未定义，浏览器会静默使用 fallback（或整条声明失效）：\n` +
        `${dead.join("\n")}\n` +
        `修法：改用已定义的变量，或把定义补进 globals.css / 内联 style 注入。`,
    ).toEqual([]);
  });

  it("扫描面与正则都真的在工作（活体证人，防「无物可查 ⇒ 恒真」）", () => {
    const { files, refNames, defs } = scan();
    expect(files).toBeGreaterThanOrEqual(50);
    // 三个已知事实，逐一钉住扫描的三个面 —— 任一面写窄，这里立刻红：
    expect(refNames.has("--right-w"), "没扫到 tsx 里 var() 的引用").toBe(true);
    expect(defs.has("--accent"), "没扫到 .css 里的变量定义").toBe(true);
    expect(defs.has("--right-w"), "没把内联注入的字符串字面量算作定义（会假红）").toBe(true);
  });

  it("掩码器：注释里提到变量名不算引用，字符串里的 var() 才算", () => {
    const count = (src: string) => {
      const masked = maskComments(src);
      const re = new RegExp(REF.source, "g");
      let n = 0;
      while (re.exec(masked) !== null) n++;
      return n;
    };
    // 注释里的提及必须被掩掉：修复代码的说明文字天然会写出问题变量名。
    expect(count("/* 原实现用 var(--color-up, #16a34a) 画发酵柱 */")).toBe(0);
    expect(count("// var(--color-down) 从未定义")).toBe(0);
    // 但真实引用（含模板字符串里的 Tailwind 任意值写法）必须被数到。
    expect(count("const c = 'var(--color-up, #16a34a)';")).toBe(1);
    expect(count('rightCollapsed ? "a" : "lg:grid-cols-[minmax(0,1fr),var(--right-w)]"')).toBe(1);
  });
});
