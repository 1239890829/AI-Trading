import { describe, expect, it } from "vitest";
import { priceLimitPct } from "./price-limit";

describe("priceLimitPct 板块涨跌幅限制", () => {
  it("沪深主板 ±10%", () => {
    expect(priceLimitPct("600519")).toBe(10);
    expect(priceLimitPct("sh600519")).toBe(10);
    // 裸 000001 = 平安银行（深主板），不是上证指数
    expect(priceLimitPct("000001")).toBe(10);
    expect(priceLimitPct("sz000001")).toBe(10);
    expect(priceLimitPct("SH600519")).toBe(10); // 大小写不敏感
  });

  it("创业板/科创板 ±20%", () => {
    expect(priceLimitPct("300750")).toBe(20);
    expect(priceLimitPct("sz300750")).toBe(20);
    expect(priceLimitPct("688981")).toBe(20);
    expect(priceLimitPct("sh688981")).toBe(20);
  });

  it("北交所 ±30%", () => {
    expect(priceLimitPct("430047")).toBe(30);
    expect(priceLimitPct("bj430047")).toBe(30);
    expect(priceLimitPct("832566")).toBe(30);
    expect(priceLimitPct("920001")).toBe(30);
  });

  it("指数无涨跌停概念 → null（调用方回退动态区间）", () => {
    expect(priceLimitPct("sh000001")).toBeNull(); // 上证指数（带前缀规范形态）
    expect(priceLimitPct("sz399107")).toBeNull(); // 深证A指
    expect(priceLimitPct("bj899050")).toBeNull(); // 北证50
    expect(priceLimitPct("399107")).toBeNull(); // 裸指数段兜底
    expect(priceLimitPct("sh880001")).toBeNull(); // 同花顺板块指数段
  });

  it("ST/*ST 仅主板/创业/科创收窄 ±5%", () => {
    expect(priceLimitPct("000001", "ST易联众")).toBe(5);
    expect(priceLimitPct("300750", "*ST某某")).toBe(5);
    expect(priceLimitPct("600519", "贵州茅台")).toBe(10); // 非 ST 不受影响
  });

  it("北交所不受 ST 5% 影响", () => {
    expect(priceLimitPct("832566", "ST某某")).toBe(30);
  });

  it("判不出 → null 不猜（三态纪律）", () => {
    expect(priceLimitPct("")).toBeNull();
    expect(priceLimitPct("ABC")).toBeNull();
    expect(priceLimitPct("sh6005199")).toBeNull(); // 非 6 位
  });
});
