/**
 * 盘口 / 逐笔视图（`IMP-038` 口径标注，2026-09-16）。
 *
 * 钉住的是**口径诚实**，不是样式：
 * ① 逐笔的口径**由数据推导**（`Trade.source`），不写死——TDX 备源给的是
 *    **3 秒快照聚合**（实测 600519 全日 3867 行、相邻时间差众数 = 3s），
 *    东财给的是逐笔明细。把"逐笔"写死在 UI 上，备源接管时口径就是错的。
 * ② 三态纪律（2026-09-08 审查 F1）不得回退：`undefined` = 加载中（骨架），
 *    `[]` = 拉过且确认无（空态文案）——把"还没拉到"渲染成"暂无"是误导。
 *
 * ⚠️ 刻意**不**断言渲染出的时间字符串：`timeText()` 按浏览器本地时区渲染，
 * 断言它会让用例随宿主时区变红（CI 跑 UTC）。时间戳口径由后端
 * `tests/test_tdx_tick.py::test_row_to_trade_uses_true_utc_not_pseudo_utc` 钉住。
 */
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { BookTradesView } from "@/components/detail/book-trades-view";
import type { Trade } from "@/types/market";

afterEach(cleanup);

function trade(over: Partial<Trade> = {}): Trade {
  return {
    symbol: "600519",
    source: "tdx",
    quality: "high",
    quality_reasons: [],
    received_at: "2026-09-16T07:30:00Z",
    ts: "2026-09-16T01:30:00Z",
    price: 1258.0,
    volume: 7,
    side: "buy",
    ...over,
  };
}

describe("逐笔口径标注", () => {
  it("TDX 源 ⇒ 标注「通达信 3 秒快照聚合」，并给出笔数", () => {
    render(
      <BookTradesView book={null} trades={[trade(), trade({ side: "sell" })]} showBook={false} />
    );
    const caption = screen.getByText(/口径/);
    expect(caption.textContent).toContain("通达信");
    expect(caption.textContent).toContain("3 秒快照聚合");
    expect(caption.textContent).toContain("2 笔");
  });

  it("东财源 ⇒ 标注「逐笔明细」，**不得**沿用 3 秒聚合口径", () => {
    render(
      <BookTradesView
        book={null}
        trades={[trade({ source: "eastmoney" })]}
        showBook={false}
      />
    );
    const caption = screen.getByText(/口径/);
    expect(caption.textContent).toContain("东方财富");
    expect(caption.textContent).toContain("逐笔明细");
    expect(caption.textContent).not.toContain("3 秒快照聚合");
  });

  it("方向标记：buy→B / sell→S / neutral→·", () => {
    render(
      <BookTradesView
        book={null}
        trades={[trade({ side: "buy" }), trade({ side: "sell" }), trade({ side: "neutral" })]}
        showBook={false}
      />
    );
    const rows = screen.getAllByRole("row");
    expect(rows.map((r) => r.lastElementChild?.textContent)).toEqual(["B", "S", "·"]);
  });

  it("量纲 = 手，原样显示（**不得**套用 fmtVolume 的股→手换算）", () => {
    // 回归背景（2026-09-16 实测）：`fmtVolume` 按"后端统一为股"除以 100，
    // 而逐笔的 `Trade.volume` 本身就是手 ⇒ 18 手被渲染成 "0"、70 手也成 "0"。
    // 此前不可见，只因逐笔端点一直是 502（从没人看到过一行数据）。
    render(
      <BookTradesView
        book={null}
        trades={[trade({ volume: 18 }), trade({ volume: 708 })]}
        showBook={false}
      />
    );
    const cells = screen.getAllByRole("row").map((r) => [...r.children][2].textContent);
    expect(cells).toEqual(["18", "708"]);
  });
});

describe("三态纪律", () => {
  it("undefined = 加载中 ⇒ 骨架，不得渲染空态文案", () => {
    const { container } = render(<BookTradesView book={null} trades={undefined} showBook={false} />);
    expect(screen.queryByText(/暂无逐笔/)).toBeNull();
    expect(container.querySelectorAll("[aria-hidden]").length).toBeGreaterThan(0);
  });

  it("[] = 拉过且确认无 ⇒ 空态文案，不渲染口径行", () => {
    render(<BookTradesView book={null} trades={[]} showBook={false} />);
    expect(screen.getByText(/暂无逐笔/)).toBeTruthy();
    expect(screen.queryByText(/口径/)).toBeNull();
  });
});

describe("盘口分支不受影响", () => {
  it("showBook + book=null ⇒ 原盘口空态文案（与逐笔无关）", () => {
    render(<BookTradesView book={null} trades={[trade()]} showBook />);
    expect(screen.getByText(/盘口数据不可用/)).toBeTruthy();
    expect(screen.queryByText(/口径/)).toBeNull();
  });
});
