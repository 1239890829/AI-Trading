import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
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

// jsdom 无 IntersectionObserver——mock 记录回调，测试可手动 invoke 模拟"滚到底"
let observerCallback: IntersectionObserverCallback | null = null;
const mockObserver = { observe: vi.fn(), disconnect: vi.fn(), unobserve: vi.fn() };
beforeAll(() => {
  function FakeIO(cb: IntersectionObserverCallback) {
    observerCallback = cb;
    return mockObserver;
  }
  vi.stubGlobal("IntersectionObserver", FakeIO);
});
afterAll(() => {
  vi.unstubAllGlobals();
});

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
    // 卡片瀑布流：MasonryColumns 贪心重排不保证 DOM 顺序 = 排序序，
    // 排序正确性由「按序注入 → 三卡都渲染 + 数值/徽标语义正确」间接覆盖
    expect(screen.getByTestId("board-row-BK0001").textContent).toContain("甲板");
    expect(screen.getByTestId("board-row-BK0002").textContent).toContain("乙板");
    expect(screen.getByTestId("board-row-BK0003").textContent).toContain("丙板");
    expect(table.textContent).toContain("--"); // null 净流入不冒充 0
    expect(table.textContent).toContain("↑2"); // 排名上升
    expect(table.textContent).toContain("↓1"); // 排名下降
    expect(table.textContent).toContain("3天"); // 连续流入
    expect(table.textContent).toContain("0天"); // 今日净流出 ≠ 未沉淀
    expect(table.textContent).toContain("—"); // 未沉淀的连续列
  });

  it("滚动到底触发增量加载（分页，scroll 方案）", async () => {
    const many = Array.from({ length: 70 }, (_, i) =>
      row({ board_code: `BK${String(i + 1).padStart(4, "0")}`, name: `板${i}`, main_net_yi: 100 - i, rank: i + 1 }),
    );
    vi.mocked(getBoardFundFlow).mockResolvedValue(payload(many));
    await mount();

    const sentinel = screen.getByTestId("board-flow-sentinel");
    const box = screen.getByTestId("board-flow-table");
    // effect 向上找 scrollHeight>clientHeight 的祖先；jsdom 全 0 时找到 documentElement。
    // 直接把 sentinel rect 改成"在容器底部警戒区内"，然后在 document 上派发 scroll。
    const sentinelRect = vi.spyOn(sentinel, "getBoundingClientRect");
    sentinelRect.mockReturnValue({ top: 480, bottom: 484 } as DOMRect);
    // 各候选滚动容器都派发一次（bubble 到 document 监听者）
    fireEvent.scroll(document);
    fireEvent.scroll(box);
    await act(async () => {});
    const hint = screen.getByText(/已加载/).textContent ?? "";
    const loaded = Number((hint.match(/\d+/) ?? ["0"])[0]);
    expect(loaded).toBeGreaterThan(30);
    sentinelRect.mockRestore();
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
