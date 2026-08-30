import { describe, expect, it } from "vitest";
import { stockRedirectTarget, workbenchUrl } from "./routing";

describe("workbenchUrl", () => {
  it("拼出带 symbol 的详情地址", () => {
    expect(workbenchUrl("600105")).toBe("/workbench?symbol=600105");
  });
});

describe("stockRedirectTarget（跨页面联动 bug 回归：路径参数此前被忽略）", () => {
  it("路径参数生效（/stock/002855 — 此前只读查询参数导致永远落默认标的）", () => {
    expect(stockRedirectTarget("002855")).toBe("/workbench?symbol=002855");
    expect(stockRedirectTarget("600103")).toBe("/workbench?symbol=600103");
  });

  it("无路径参数时回退查询参数（/stock?symbol=600103 兼容形态）", () => {
    expect(stockRedirectTarget(undefined, "600103")).toBe("/workbench?symbol=600103");
    expect(stockRedirectTarget("", "600103")).toBe("/workbench?symbol=600103");
  });

  it("两者都有时路径参数优先", () => {
    expect(stockRedirectTarget("002855", "600103")).toBe("/workbench?symbol=002855");
  });

  it("剥掉市场前后缀（600105.SH / SH600105 → 600105）", () => {
    expect(stockRedirectTarget("600105.SH")).toBe("/workbench?symbol=600105");
    expect(stockRedirectTarget("SH600105")).toBe("/workbench?symbol=600105");
  });

  it("无法解析时返回 null（由调用方落到 /workbench 默认页）", () => {
    expect(stockRedirectTarget()).toBeNull();
    expect(stockRedirectTarget("")).toBeNull();
    expect(stockRedirectTarget("abc")).toBeNull();
    expect(stockRedirectTarget("ST")).toBeNull();
  });

  it("超长数字截断到 6 位", () => {
    expect(stockRedirectTarget("600105123")).toBe("/workbench?symbol=600105");
  });
});
