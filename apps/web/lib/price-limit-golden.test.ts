// @vitest-environment node
/**
 * R4 前后端指标黄金样本对照——涨跌停幅度（前端侧）。
 *
 * 共享样本：backend/tests/golden/price_limit_golden.json（后端 pytest
 * test_price_limit_golden.py 消费同一文件）。任一端口径变更必须显式
 * 更新样本并双端同批提交——测试红即口径漂移。divergent 样本是设计内
 * 已知分歧（halt_risk ST 决策后统一），照常断言各自行为。
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import { priceLimitPct } from "./price-limit";

interface GoldenCase {
  symbol: string;
  name?: string | null;
  be?: number | null;
  fe: number | null;
  note?: string;
  divergent?: string;
}

const goldenPath = resolve(__dirname, "../../../backend/tests/golden/price_limit_golden.json");
const golden = JSON.parse(readFileSync(goldenPath, "utf-8")) as { cases: GoldenCase[] };

describe("price-limit golden samples (R4 跨端共享)", () => {
  it.each(golden.cases.filter((c) => c.fe !== null))(
    "$symbol ($name) → $fe%",
    (c) => {
      expect(priceLimitPct(c.symbol, c.name ?? undefined)).toBe(c.fe);
    },
  );

  it("fe=null 的样本（指数段/判不出）必须返回 null（三态，不猜）", () => {
    for (const c of golden.cases.filter((x) => x.fe === null)) {
      expect(priceLimitPct(c.symbol, c.name ?? undefined)).toBeNull();
    }
  });

  it("黄金样本覆盖关键代码段且分歧样本显式留档", () => {
    const syms = new Set(golden.cases.map((c) => c.symbol));
    // 302132 / 889123 / 870804 为 2026-09-11 补的 required 段：
    // 302 与 88 段此前无样本 ⇒ 双端已实际漂移却不报警。删样本 = 关掉报警器。
    for (const required of [
      "600519", "300750", "688981", "920075", "300999", "sh000001",
      "302132", "889123", "870804",
    ]) {
      expect(syms.has(required)).toBe(true);
    }
    expect(golden.cases.some((c) => c.divergent)).toBe(true);
  });
});
