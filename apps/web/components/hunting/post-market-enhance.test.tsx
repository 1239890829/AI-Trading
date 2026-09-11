import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { LurkTable } from "@/components/hunting/post-market-enhance";
import type { LurkPoolPayload } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(cleanup);

// 与组件内 dateText 同构（确认日仅做展示，不参与断言口径）
const dateText = (ms: number) => {
  const d = new Date(ms);
  return `${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
};

const STALE_NOTE =
  "池基于 2026-09-03 的 K 线，滞后 6 个交易日（阈值 3，含阈值内属正常）——" +
  "marketdb 停更时先跑 scripts/sync_marketdb.py";

function payload(over: Partial<LurkPoolPayload> = {}): LurkPoolPayload {
  return {
    trade_date: "2026-09-10",
    as_of: "2026-09-10",
    stale_days: 0,
    stale: false,
    stale_note: "",
    items: [{ symbol: "000058", confirm_ms: 1788969600000 }],
    ...over,
  };
}

describe("潜伏观察池 · 陈旧披露（2026-09-11）", () => {
  it("新鲜态：常驻「数据截至」，不出现任何滞后措辞", () => {
    render(<LurkTable s={{ status: "ready", data: payload() }} date={dateText} />);
    expect(screen.getByText(/数据截至 2026-09-10/)).toBeTruthy();
    expect(screen.queryByText(/滞后/)).toBeNull();
  });

  it("阈值内滞后：补一句「阈值内属正常」，不升级为告警", () => {
    render(<LurkTable s={{ status: "ready", data: payload({ stale_days: 1 }) }} date={dateText} />);
    expect(screen.getByText(/滞后 1 个交易日，阈值内属正常/)).toBeTruthy();
    expect(screen.queryByText(/sync_marketdb\.py/)).toBeNull();
  });

  it("停更超阈值：显式告警，**且仍照常渲染 items**（只披露、不隐藏）", () => {
    render(
      <LurkTable
        s={{
          status: "ready",
          data: payload({ as_of: "2026-09-03", stale_days: 6, stale: true, stale_note: STALE_NOTE }),
        }}
        date={dateText}
      />
    );
    expect(screen.getByText(STALE_NOTE)).toBeTruthy();
    expect(screen.getByText("000058")).toBeTruthy();
    // 告警态不再重复「阈值内属正常」——两种措辞互斥。
    // ⚠️ 必须用**完整句式**匹配：`stale_note` 正文里也有「阈值内属正常」四个字
    //    （「阈值 3，含阈值内属正常」），宽泛正则会把告警正文自己匹配上（本轮踩过）。
    expect(screen.queryByText(/滞后 6 个交易日，阈值内属正常/)).toBeNull();
  });

  it("空池 + 停更：披露不得被空态吞掉（2026-09-11 回归：原实现在空池处提前 return）", () => {
    render(
      <LurkTable
        s={{
          status: "ready",
          data: payload({ items: [], as_of: "2026-09-03", stale_days: 6, stale: true, stale_note: STALE_NOTE }),
        }}
        date={dateText}
      />
    );
    expect(screen.getByText(/当前无「潜伏\+试盘回踩确认」观察票。/)).toBeTruthy();
    // 关键：空态下「数据截至」与停更告警必须仍在（否则"今日无票"会被误当今天的结论）
    expect(screen.getByText(/数据截至 2026-09-03/)).toBeTruthy();
    expect(screen.getByText(STALE_NOTE)).toBeTruthy();
  });

  it("加载中 / 失败：不渲染披露（尚无数据，不得虚构口径）", () => {
    const { container } = render(<LurkTable s={{ status: "loading" }} date={dateText} />);
    expect(container.textContent).toContain("加载中");
    cleanup();
    render(<LurkTable s={{ status: "error", msg: "boom" }} date={dateText} />);
    expect(screen.getByText("boom")).toBeTruthy();
    expect(screen.queryByText(/数据截至/)).toBeNull();
  });
});
