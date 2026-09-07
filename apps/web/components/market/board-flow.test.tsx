import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { BoardFlowPanel } from "@/components/market/board-flow";
import { getBoardFlowMembers, getBoardFlowMinute, getBoardFundFlow, type BoardFlowRow } from "@/lib/api";

// 板块资金流（L2 主视图）：排序纯内存（null 殿后不冒充 0）、筛选 chips、
// 点行开下钻抽屉（分钟累计 + 成员排行 + 关闭即停轮询）。

vi.mock("@/lib/api", () => ({
  getBoardFundFlow: vi.fn(),
  getBoardFlowMinute: vi.fn(),
  getBoardFlowMembers: vi.fn(),
}));

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

beforeEach(() => {
  vi.mocked(getBoardFundFlow).mockReset();
  vi.mocked(getBoardFlowMinute).mockReset();
  vi.mocked(getBoardFlowMembers).mockReset();
});

function row(over: Partial<BoardFlowRow>): BoardFlowRow {
  return {
    board_code: "BK0001",
    name: "通信技术",
    kind: "concept",
    change_pct: 1.65,
    main_net_yi: 50,
    main_net_ratio: 9.79,
    super_net_yi: 40,
    main_net_5d_yi: -30,
    main_net_10d_yi: -10,
    leader_name: "美格智能",
    leader_symbol: "002881",
    streak: 3,
    rank: 1,
    rank_delta: 2,
    ...over,
  };
}

const payload = (rows: BoardFlowRow[], over: Record<string, unknown> = {}) => ({
  available: true,
  kind: "concept" as const,
  range: "intraday" as const,
  rows,
  total_boards: 504,
  coverage: null,
  updated_at: "10:30:00",
  rank_basis_date: "2026-09-04",
  degraded: [],
  ...over,
});

async function mount() {
  render(<BoardFlowPanel />);
  await act(async () => {}); // flush usePollingFetch 首拍
}

describe("BoardFlowPanel", () => {
  it("按净流入降序渲染，null 殿后显示 --，榜位/排名Δ 正确", async () => {
    vi.mocked(getBoardFundFlow).mockResolvedValue(payload([
      row({ board_code: "BK0003", name: "丙板", main_net_yi: null, rank_delta: null, streak: null, rank: 3 }),
      row({ board_code: "BK0002", name: "乙板", main_net_yi: -20, rank: 2, rank_delta: -1, streak: 0 }),
      row({ board_code: "BK0001", name: "甲板", main_net_yi: 50, rank: 1, rank_delta: 2 }),
    ]));
    await mount();

    expect(getBoardFundFlow).toHaveBeenCalledWith("concept", "intraday");
    const table = screen.getByTestId("board-flow-table");
    const codes = [...table.querySelectorAll("tr")].map((tr) => tr.textContent ?? "").join("|");
    // 甲(+50) → 乙(-20) → 丙(null 殿后)
    expect(codes.indexOf("甲板")).toBeLessThan(codes.indexOf("乙板"));
    expect(codes.indexOf("乙板")).toBeLessThan(codes.indexOf("丙板"));
    expect(table.textContent).toContain("--"); // null 净流入不冒充 0
    expect(table.textContent).toContain("↑2"); // 排名上升
    expect(table.textContent).toContain("↓1"); // 排名下降
    expect(table.textContent).toContain("3天"); // 连续流入
    expect(table.textContent).toContain("0天"); // 今日净流出 ≠ 未沉淀
    expect(table.textContent).toContain("—"); // 未沉淀的连续列
  });

  it("筛选 chip『净流入>0』剔除净流出板块", async () => {
    vi.mocked(getBoardFundFlow).mockResolvedValue(payload([
      row({ board_code: "BK0001", name: "甲板", main_net_yi: 50 }),
      row({ board_code: "BK0002", name: "乙板", main_net_yi: -20 }),
    ]));
    await mount();
    fireEvent.click(screen.getByText("净流入>0"));
    const table = screen.getByTestId("board-flow-table");
    expect(table.textContent).toContain("甲板");
    expect(table.textContent).not.toContain("乙板");
  });

  it("available=false → 显示 reason，不渲染表格", async () => {
    vi.mocked(getBoardFundFlow).mockResolvedValue(
      payload([], { available: false, reason: "东财板块列表不可用（双域已重试）", rows: [] }),
    );
    await mount();
    expect(screen.getByText(/东财板块列表不可用/)).toBeTruthy();
    expect(screen.queryByTestId("board-flow-table")).toBeNull();
  });

  it("degraded 显式透传（主域不可达延迟口径）", async () => {
    vi.mocked(getBoardFundFlow).mockResolvedValue(
      payload([row({})], { degraded: ["主域不可达，使用延迟口径（push2delay）"] }),
    );
    await mount();
    expect(screen.getByText(/延迟口径/)).toBeTruthy();
  });

  it("点行开下钻抽屉：分钟累计 + 成员排行，关闭按钮可关", async () => {
    vi.mocked(getBoardFundFlow).mockResolvedValue(payload([row({ board_code: "BK1650", name: "通信技术" })]));
    vi.mocked(getBoardFlowMinute).mockResolvedValue({
      available: true,
      board_code: "BK1650",
      items: [
        { t: "09:31", main: 2.1, small: -0.7, mid: -1.4, big: 0.4, super_: 1.7 },
        { t: "09:32", main: 12.5, small: -4.1, mid: -8.4, big: 0.5, super_: 12.0 },
      ],
      daily_bars: [
        { date: "2026-09-05", main_yi: 1.5, close_pct: 1.1 },
        { date: "2026-09-06", main_yi: -2.5, close_pct: -0.3 },
      ],
      delayed: true,
      updated_at: "10:30:00",
      degraded: [],
    });
    vi.mocked(getBoardFlowMembers).mockResolvedValue({
      available: true,
      board_code: "BK1650",
      rows: [
        { symbol: "300308", name: "中际旭创", price: 86.09, change_pct: 5.76, main_net_yi: 22.9, super_net_yi: 20.9, big_net_yi: 2.0, main_net_ratio: 19.99 },
      ],
      delayed: false,
      updated_at: "10:30:00",
      degraded: [],
    });
    await mount();

    fireEvent.click(screen.getByTestId("board-row-BK1650"));
    expect(screen.getByTestId("board-flow-drawer")).toBeTruthy();
    expect(getBoardFlowMinute).toHaveBeenCalledWith("BK1650");
    await act(async () => {});
    expect(screen.getByTestId("board-daily-bars")).toBeTruthy();
    const drawer = screen.getByTestId("board-flow-drawer");
    expect(drawer.textContent).toContain("中际旭创");
    expect(drawer.textContent).toContain("延迟约 15 分钟口径"); // 延迟口径必须标注
    // 成员代码 → 工作台跳转（统一走 StockLink：href 带 from 参数，锚前缀断言）
    const link = drawer.querySelector('a[href^="/workbench?symbol=300308"]');
    expect(link).toBeTruthy();

    fireEvent.click(screen.getByTestId("drawer-close"));
    expect(screen.queryByTestId("board-flow-drawer")).toBeNull();
  });
});
