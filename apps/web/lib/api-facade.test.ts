import { readFileSync, readdirSync } from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

/**
 * `lib/api.ts` 门面守卫（IMP-005，2026-09-15 过厚模块切片配套）。
 *
 * ## 为什么需要它
 * 切片（2752 行 → 14 个业务域分片）的收益**只在"门面保持薄"时才成立**：
 * 只要有人往门面里塞回一个实现，`lib/api.ts` 就会重新变厚，而**所有测试仍会全绿**
 * —— 因为功能没坏。这是典型的"改回去也没人拦"的形态（[[KB-ENG-72]] 同族：
 * 清单/判据覆盖了什么 ≠ 真实覆盖面）。
 *
 * ## 判据（结构钉，不枚举业务名）
 * 1. **门面只做转发**：非注释行只允许 `export * from "./api/<域>";`。
 * 2. **双向闭包**：`lib/api/*.ts` 每个分片都必须被门面转发；门面转发的每个目标都必须真实存在。
 *    这条同时抓两种漂移：新增分片忘了转发（名字取不到）与转发指向已删文件（构建期才炸）。
 * 3. **`internal.ts` 不得进公开面**：它是切片时提升出可见性的模块内助手
 *    （`request` / `DEFAULT_TIMEOUT_MS` / `getJson*` / `sendJson`），转发它会让
 *    `lib/api.ts` 的导出面悄悄变大。
 */

const LIB = path.resolve(import.meta.dirname);
const FACADE = path.join(LIB, "api.ts");
const API_DIR = path.join(LIB, "api");

/** 允许出现在门面里的行：注释、空行、`export * from "./api/x";`。 */
const FORWARD = /^export \* from "\.\/api\/([a-z0-9-]+)";$/;

/** 纯函数判据：返回违规说明（空数组 = 合规）—— 便于自证"能变红"。 */
export function facadeViolations(source: string, shardFiles: readonly string[]): string[] {
  const bad: string[] = [];
  const targets: string[] = [];
  const lines = source.split("\n");

  lines.forEach((line, idx) => {
    const t = line.trim();
    if (!t) return;
    if (t.startsWith("*") || t.startsWith("//") || t.startsWith("/**") || t.startsWith("*/")) return;
    const m = FORWARD.exec(t);
    if (m) {
      targets.push(m[1]);
      return;
    }
    bad.push(`第 ${idx + 1} 行不是注释也不是转发语句：${t.slice(0, 60)}`);
  });

  if (lines.length > 40) bad.push(`门面 ${lines.length} 行 > 40：门面正在重新变厚`);

  const shards = shardFiles
    .filter((f) => f.endsWith(".ts"))
    .map((f) => f.replace(/\.ts$/, ""));
  for (const s of shards) {
    if (s === "internal") continue;
    if (!targets.includes(s)) bad.push(`分片 ${s}.ts 没有被门面转发（新增分片必须登记）`);
  }
  for (const t of targets) {
    if (!shards.includes(t)) bad.push(`门面转发了不存在的分片 ${t}`);
    if (t === "internal") bad.push("门面转发了 internal（模块内助手不得进公开面）");
  }
  return bad;
}

describe("lib/api 门面只做转发", () => {
  it("门面当前合规（只转发 + 双向闭包 + 不含 internal）", () => {
    const violations = facadeViolations(readFileSync(FACADE, "utf8"), readdirSync(API_DIR));
    expect(violations, violations.join("\n")).toEqual([]);
  });

  it("分片目录确实非空（防「目录被清空 ⇒ 上面那条恒真」）", () => {
    const shards = readdirSync(API_DIR).filter((f) => f.endsWith(".ts") && f !== "internal.ts");
    expect(shards.length).toBeGreaterThanOrEqual(10);
  });

  it("判据自证：四种违规都能被抓出来（不是恒真的摆设）", () => {
    // ⚠️ shardFiles 的契约与 `readdirSync` 一致：**带 .ts 后缀**（函数内部按后缀过滤）
    const shards = ["client.ts", "market.ts", "internal.ts"];
    // ① 门面里塞回实现（假样本必须凑齐全部分片，否则会多报一条"漏转发"而看不出是哪条生效）
    expect(
      facadeViolations(
        'export * from "./api/client";\nexport * from "./api/market";\nexport function getX() { return 1; }\n',
        shards,
      ),
    ).toEqual(["第 3 行不是注释也不是转发语句：export function getX() { return 1; }"]);
    // ② 新增分片忘了转发
    expect(facadeViolations('export * from "./api/client";\n', shards)).toContain(
      "分片 market.ts 没有被门面转发（新增分片必须登记）",
    );
    // ③ 转发指向不存在的分片
    expect(facadeViolations('export * from "./api/client";\nexport * from "./api/ghost";\n', shards)).toContain(
      "门面转发了不存在的分片 ghost",
    );
    // ④ 把内部助手也转发出去
    expect(
      facadeViolations('export * from "./api/client";\nexport * from "./api/internal";\n', shards),
    ).toContain("门面转发了 internal（模块内助手不得进公开面）");
  });
});
