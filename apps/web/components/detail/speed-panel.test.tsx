import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SpeedPanel } from "@/components/detail/speed-panel";
import { getSpeedRank, getThemesCatalog, type SpeedRankPayload } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(cleanup);

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getThemesCatalog: vi.fn(),
    getSpeedRank: vi.fn(),
  };
});

const mockedCatalog = vi.mocked(getThemesCatalog);
const mockedSpeed = vi.mocked(getSpeedRank);

const catalog = [
  { code: "881156.TI", name: "粮食概念" },
  { code: "885431.TI", name: "机器人概念" },
];

const payload = (rows: SpeedRankPayload["items"]): SpeedRankPayload => ({
  theme: "881156.TI",
  theme_name: "粮食概念",
  window: "5m",
  basis: "涨速 = 最近 5 分钟涨跌幅（同花顺行情口径）",
  items: rows,
});

describe("SpeedPanel 涨速榜", () => {
  it("口径标注必须可见（同花顺口径，不构成买卖建议）——防涨速定义漂移", async () => {
    mockedCatalog.mockResolvedValue(catalog);
    mockedSpeed.mockResolvedValue(payload([
      { symbol: "600105", name: "永鼎股份", price: 40.28, change_pct: 1.21, speed: 2.35, sampled: true },
    ]));
    render(<SpeedPanel />);
    await waitFor(() => expect(screen.getByText("永鼎股份")).toBeTruthy());
    const t = document.body.textContent ?? "";
    expect(t).toContain("最近 5 分钟涨跌幅");
    expect(t).toContain("不构成买卖建议");
  });

  it("sampled=false 的标的显示「采样中」而非伪造的 0.00%（契约：绝不臆造涨速）", async () => {
    mockedCatalog.mockResolvedValue(catalog);
    mockedSpeed.mockResolvedValue(payload([
      { symbol: "600105", name: "永鼎股份", price: 40.28, change_pct: 1.21, speed: null, sampled: false, sample_span_sec: 95 },
    ]));
    render(<SpeedPanel />);
    await waitFor(() => expect(screen.getByText("采样中…")).toBeTruthy());
    expect(screen.queryByText("0.00%")).toBeNull();
  });

  it("题材下拉切换 → 用新 theme 重新请求", async () => {
    mockedCatalog.mockResolvedValue(catalog);
    mockedSpeed.mockResolvedValue(payload([]));
    render(<SpeedPanel />);
    await waitFor(() => expect((screen.getByLabelText("选择题材") as HTMLSelectElement).value).toBe("881156.TI"));
    expect(mockedSpeed).toHaveBeenCalledWith("881156.TI");
    fireEvent.change(screen.getByLabelText("选择题材"), { target: { value: "885431.TI" } });
    await waitFor(() => expect(mockedSpeed).toHaveBeenCalledWith("885431.TI"));
  });

  it("后端 note（题材成分未同步）透传显示", async () => {
    mockedCatalog.mockResolvedValue(catalog);
    mockedSpeed.mockResolvedValue({ ...payload([]), note: "题材成分尚未同步，稍后再试" });
    render(<SpeedPanel />);
    await waitFor(() => expect(screen.getByText("题材成分尚未同步，稍后再试")).toBeTruthy());
  });
});
