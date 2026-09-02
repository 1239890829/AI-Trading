import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { SearchBox } from "@/components/search-box";
import { ApiError, searchSymbols } from "@/lib/api";
import type { SymbolSearchItem } from "@/types/market";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(cleanup);

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

vi.mock("@/lib/api", () => {
  // 组件用 instanceof ApiError 区分错误文案，mock 必须提供同名类
  class ApiError extends Error {
    constructor(
      readonly status: number,
      message: string,
      readonly code = "http_error",
    ) {
      super(message);
    }
  }
  return { ApiError, addToWatchlist: vi.fn(), searchSymbols: vi.fn() };
});

const item = (symbol: string, name = "测试股"): SymbolSearchItem => ({
  symbol,
  name,
  market: "SH",
  source: "test",
  is_realtime: false,
});

function typeQuery(value: string) {
  const input = screen.getByPlaceholderText("搜索代码 / 名称");
  fireEvent.change(input, { target: { value } });
  return input;
}

beforeEach(() => {
  vi.mocked(searchSymbols).mockReset();
});

describe("SearchBox 搜索体验（防抖/竞态/loading/失败/空结果）", () => {
  it("防抖：输入后 250ms 内不发请求，停稳后才发出", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(searchSymbols).mockResolvedValue([item("600519", "贵州茅台")]);
      render(<SearchBox />);
      typeQuery("600519");
      act(() => {
        vi.advanceTimersByTime(249);
      });
      expect(searchSymbols).not.toHaveBeenCalled();
      await act(async () => {
        vi.advanceTimersByTime(1);
      });
      expect(searchSymbols).toHaveBeenCalledWith("600519");
    } finally {
      vi.useRealTimers();
    }
  });

  it("Enter 跳过防抖立即搜索（旧实现结果未回时回车被吞，表现为无响应）", () => {
    vi.useFakeTimers();
    try {
      vi.mocked(searchSymbols).mockResolvedValue([]);
      render(<SearchBox />);
      typeQuery("600519");
      expect(searchSymbols).not.toHaveBeenCalled();
      fireEvent.keyDown(screen.getByPlaceholderText("搜索代码 / 名称"), { key: "Enter" });
      expect(searchSymbols).toHaveBeenCalledWith("600519");
    } finally {
      vi.useRealTimers();
    }
  });

  it("竞态：慢的旧响应后到，不覆盖新关键词的结果（后发先至丢弃）", async () => {
    vi.useFakeTimers();
    try {
      const resolvers: ((v: SymbolSearchItem[]) => void)[] = [];
      vi.mocked(searchSymbols).mockImplementation(
        () => new Promise<SymbolSearchItem[]>((res) => resolvers.push(res)),
      );
      render(<SearchBox />);
      typeQuery("600"); // 请求 1
      await act(async () => {
        vi.advanceTimersByTime(250);
      });
      typeQuery("600519"); // 请求 2（cleanup 作废请求 1 的 seq）
      await act(async () => {
        vi.advanceTimersByTime(250);
      });
      expect(searchSymbols).toHaveBeenNthCalledWith(1, "600");
      expect(searchSymbols).toHaveBeenNthCalledWith(2, "600519");
      // 请求 2 先返回：显示新结果
      await act(async () => {
        resolvers[1]([item("600519", "贵州茅台")]);
      });
      expect(screen.getByText("贵州茅台")).toBeTruthy();
      // 请求 1 后返回（慢源）：必须被丢弃，列表不变
      await act(async () => {
        resolvers[0]([item("600001")]);
      });
      expect(screen.getByText("贵州茅台")).toBeTruthy();
      expect(screen.queryByText("600001")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("loading：请求期间显示「搜索中…」与 aria-busy，返回后消失", async () => {
    vi.useFakeTimers();
    try {
      let resolve!: (v: SymbolSearchItem[]) => void;
      vi.mocked(searchSymbols).mockImplementation(
        () => new Promise<SymbolSearchItem[]>((res) => (resolve = res)),
      );
      render(<SearchBox />);
      typeQuery("600519");
      await act(async () => {
        vi.advanceTimersByTime(250);
      });
      expect(screen.getByText("搜索中…")).toBeTruthy();
      const input = screen.getByPlaceholderText("搜索代码 / 名称");
      expect(input.getAttribute("aria-busy")).toBe("true");
      await act(async () => {
        resolve([item("600519", "贵州茅台")]);
      });
      expect(screen.queryByText("搜索中…")).toBeNull();
      expect(input.getAttribute("aria-busy")).toBe("false");
      expect(screen.getByText("贵州茅台")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("失败提示：请求 reject 时在下拉面板显示错误文案（旧实现静默吞错）", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(searchSymbols).mockRejectedValue(new ApiError(502, "搜索失败：上游数据源异常", "http_502"));
      render(<SearchBox />);
      typeQuery("600519");
      await act(async () => {
        vi.advanceTimersByTime(250);
      });
      expect(screen.getByText("搜索失败：上游数据源异常", { exact: false })).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("非 ApiError 的异常显示通用失败文案", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(searchSymbols).mockRejectedValue(new Error("boom"));
      render(<SearchBox />);
      typeQuery("600519");
      await act(async () => {
        vi.advanceTimersByTime(250);
      });
      expect(screen.getByText("搜索失败，请稍后重试")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("空结果：搜过且无匹配时显示未找到提示（旧实现什么都不渲染）", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(searchSymbols).mockResolvedValue([]);
      render(<SearchBox />);
      typeQuery("zzzzz");
      await act(async () => {
        vi.advanceTimersByTime(250);
      });
      expect(screen.getByText("未找到与「zzzzz」匹配的股票")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });

  it("关键词少于 2 字不发请求，也不显示面板", () => {
    vi.useFakeTimers();
    try {
      render(<SearchBox />);
      typeQuery("6");
      act(() => {
        vi.advanceTimersByTime(500);
      });
      expect(searchSymbols).not.toHaveBeenCalled();
      expect(screen.queryByText("搜索中…")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("IME 组合期间不搜索、选字 Enter 不搜索（拼音片段是垃圾查询，会消耗上游配额）", () => {
    vi.useFakeTimers();
    try {
      vi.mocked(searchSymbols).mockResolvedValue([]);
      render(<SearchBox />);
      const input = screen.getByPlaceholderText("搜索代码 / 名称");
      fireEvent.compositionStart(input);
      fireEvent.change(input, { target: { value: "xingwang" } }); // 组合中的拼音片段
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(searchSymbols).not.toHaveBeenCalled();
      fireEvent.keyDown(input, { key: "Enter" }); // 选字上屏的 Enter，不是搜索指令
      expect(searchSymbols).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("组合结束立即用最终上屏词搜索（跳过防抖，星网锐捷场景）", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(searchSymbols).mockResolvedValue([item("002396", "星网锐捷")]);
      render(<SearchBox />);
      const input = screen.getByPlaceholderText("搜索代码 / 名称");
      fireEvent.compositionStart(input);
      fireEvent.change(input, { target: { value: "星网锐捷" } }); // IME 上屏直接替换值
      act(() => {
        vi.advanceTimersByTime(1000);
      });
      expect(searchSymbols).not.toHaveBeenCalled(); // 组合期间保持静默
      fireEvent.compositionEnd(input);
      expect(searchSymbols).toHaveBeenCalledWith("星网锐捷"); // 立即搜索，不等 250ms
      await act(async () => {});
      expect(screen.getByText("星网锐捷")).toBeTruthy();
    } finally {
      vi.useRealTimers();
    }
  });
});
