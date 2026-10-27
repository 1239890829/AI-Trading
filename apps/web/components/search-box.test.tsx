import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { SearchBox } from "@/components/search-box";
import { ApiError, addToWatchlist, searchSymbols } from "@/lib/api";
import type { SymbolSearchItem } from "@/types/market";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(cleanup);

vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));
const detail = vi.hoisted(() => ({open: vi.fn()}));
vi.mock("@/components/detail/symbol-detail-context", () => ({useSymbolDetail: () => detail}));

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
  vi.mocked(addToWatchlist).mockReset();
  detail.open.mockClear();
});

describe("SearchBox 搜索体验（防抖/竞态/loading/失败/空结果）", () => {
  it("错误结果关闭时保留最后正文直到退出结束，焦点和查询立即退出", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(searchSymbols).mockRejectedValue(new ApiError(503, "源暂不可用", "source_unavailable"));
      const {container} = render(<SearchBox />);
      const input = typeQuery("600127");
      await act(async () => vi.advanceTimersByTime(250));
      const panel = screen.getByRole("region", {name:"证券搜索结果"});
      const error = screen.getByRole("alert");
      fireEvent.click(screen.getByRole("button", {name:"关闭搜索结果"}));
      expect(screen.queryByRole("region", {name:"证券搜索结果"})).toBeNull();
      expect(panel.hasAttribute("inert")).toBe(true);
      expect(panel.contains(error)).toBe(true);
      expect(panel.textContent).toContain("源暂不可用");
      expect(document.activeElement).toBe(input);
      await act(async () => vi.advanceTimersByTime(159));
      expect(container.querySelector(".search-popover")).toBe(panel);
      await act(async () => vi.advanceTimersByTime(1));
      expect(container.querySelector(".search-popover")).toBeNull();
      expect(searchSymbols).toHaveBeenCalledTimes(1);
    } finally {vi.useRealTimers();}
  });

  it("关闭结果立即恢复搜索焦点并保留不可操作退出壳；快速重开不被旧退出计时移除", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(searchSymbols).mockResolvedValue([item("600519", "贵州茅台")]);
      const {container} = render(<SearchBox />);
      const input = typeQuery("600519");
      await act(async () => vi.advanceTimersByTime(250));
      fireEvent.click(screen.getByRole("button", {name:"关闭搜索结果"}));
      expect(screen.queryByRole("region", {name:"证券搜索结果"})).toBeNull();
      expect(document.activeElement).toBe(input);
      expect(container.querySelector(".search-popover")?.hasAttribute("inert")).toBe(true);
      fireEvent.focus(input);
      await act(async () => vi.advanceTimersByTime(200));
      expect(screen.getByRole("region", {name:"证券搜索结果"})).toBeTruthy();
      expect(searchSymbols).toHaveBeenCalledTimes(1);
    } finally {vi.useRealTimers();}
  });

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

describe("SearchBox 当前查询与写回执", () => {
  it("a new query cannot submit the old first result on Enter", async () => {
    vi.useFakeTimers();
    try {
      vi.mocked(searchSymbols).mockResolvedValueOnce([item("600519", "贵州茅台")]).mockResolvedValueOnce([]);
      render(<SearchBox />);
      typeQuery("600519");
      await act(async () => { vi.advanceTimersByTime(250); });
      expect(screen.getByText("贵州茅台")).toBeTruthy();
      const input = typeQuery("600127");
      fireEvent.keyDown(input, {key: "Enter"});
      expect(searchSymbols).toHaveBeenLastCalledWith("600127");
      expect((input as HTMLInputElement).value).toBe("600127");
      expect(screen.queryByText("贵州茅台")).toBeNull();
      await act(async () => {});
    } finally { vi.useRealTimers(); }
  });
  it("shows failed save and does not mark a stock as watched before server confirmation", async () => {
    vi.useFakeTimers();
    try {
      const {addToWatchlist} = await import("@/lib/api");
      vi.mocked(addToWatchlist).mockRejectedValueOnce(new Error("写权限不足"));
      vi.mocked(searchSymbols).mockResolvedValue([item("600127", "金健米业")]);
      render(<SearchBox />); typeQuery("600127");
      await act(async () => { vi.advanceTimersByTime(250); });
      await act(async () => { fireEvent.click(screen.getByRole("button", {name: "加入自选 金健米业"})); });
      expect(screen.getByText("加入自选失败：写权限不足")).toBeTruthy();
      expect(screen.queryByText("已加自选 ✓")).toBeNull();
    } finally { vi.useRealTimers(); }
  });
  it("closing search invalidates the outstanding response", async () => {
    vi.useFakeTimers();
    try {
      let finish!: (items: SymbolSearchItem[]) => void;
      vi.mocked(searchSymbols).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
      render(<SearchBox />); const input = typeQuery("600127");
      await act(async () => { vi.advanceTimersByTime(250); });
      fireEvent.keyDown(input, {key: "Escape"});
      await act(async () => { finish([item("600127", "金健米业")]); });
      expect(screen.queryByText("金健米业")).toBeNull();
    } finally { vi.useRealTimers(); }
  });
});

describe("搜索浮层关闭与焦点", () => {
  it("点击外部后迟到响应不会重新打开浮层", async () => {
    vi.useFakeTimers();
    try {
      let finish!: (items: SymbolSearchItem[]) => void;
      vi.mocked(searchSymbols).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
      render(<><SearchBox /><button>其他操作</button></>);
      typeQuery("600127");
      await act(async () => { vi.advanceTimersByTime(250); });
      fireEvent.mouseDown(screen.getByRole("button", {name:"其他操作"}));
      await act(async () => { finish([item("600127", "金健米业")]); });
      expect(screen.queryByText("金健米业")).toBeNull();
      expect(screen.getByRole("textbox").getAttribute("aria-busy")).toBe("false");
    } finally { vi.useRealTimers(); }
  });

  it.each(["close", "escape"])("%s 从结果区关闭后回焦输入且不重开", async mode => {
    vi.useFakeTimers();
    try {
      vi.mocked(searchSymbols).mockResolvedValue([item("600127", "金健米业")]);
      render(<SearchBox />);
      const input = typeQuery("600127");
      await act(async () => { vi.advanceTimersByTime(250); });
      const control = mode === "close" ? screen.getByRole("button", {name:"关闭搜索结果"}) : screen.getByRole("button", {name:"加入自选 金健米业"});
      control.focus();
      if (mode === "close") fireEvent.click(control);
      else fireEvent.keyDown(control, {key:"Escape"});
      expect(document.activeElement).toBe(input);
      expect(screen.queryByRole("region", {name:"证券搜索结果"})).toBeNull();
      expect((input as HTMLInputElement).value).toBe("600127");
    } finally { vi.useRealTimers(); }
  });
});


it("离开输入框后取消尚未发出的防抖搜索", async () => {
  vi.useFakeTimers();
  try {
    vi.mocked(searchSymbols).mockResolvedValue([item("600127", "金健米业")]);
    render(<><SearchBox /><button>其他操作</button></>);
    typeQuery("600127");
    fireEvent.mouseDown(screen.getByRole("button", {name:"其他操作"}));
    await act(async () => { vi.advanceTimersByTime(300); });
    expect(searchSymbols).not.toHaveBeenCalled();
    expect(screen.queryByRole("region", {name:"证券搜索结果"})).toBeNull();
  } finally { vi.useRealTimers(); }
});


it("中文组合输入的Escape不冒泡关闭搜索结果", async () => {
  vi.useFakeTimers();
  try {
    vi.mocked(searchSymbols).mockResolvedValue([item("600127", "金健米业")]);
    render(<SearchBox />);
    const input = typeQuery("600127");
    await act(async () => { vi.advanceTimersByTime(250); });
    fireEvent.compositionStart(input);
    fireEvent.keyDown(input, {key:"Escape"});
    expect(screen.getByRole("region", {name:"证券搜索结果"})).toBeTruthy();
    expect(searchSymbols).toHaveBeenCalledTimes(1);
  } finally { vi.useRealTimers(); }
});


it("折叠搜索就地展开并聚焦；收回后不接受迟到响应", async () => {
  vi.useFakeTimers();
  try {
    let finish!: (items: SymbolSearchItem[]) => void;
    vi.mocked(searchSymbols).mockImplementationOnce(() => new Promise(resolve => { finish = resolve; }));
    render(<SearchBox collapsible />);
    const trigger = screen.getByRole("button", {name:"展开证券搜索"});
    expect(screen.queryByRole("textbox")).toBeNull();
    fireEvent.click(trigger);
    const input = typeQuery("600127");
    expect(document.activeElement).toBe(input);
    await act(async () => { vi.advanceTimersByTime(250); });
    fireEvent.click(screen.getByRole("button", {name:"收起证券搜索"}));
    expect(document.activeElement).toBe(trigger);
    await act(async () => { finish([item("600127", "金健米业")]); });
    expect(screen.queryByText("金健米业")).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
  } finally { vi.useRealTimers(); }
});

it("方向键浏览多条搜索结果，打开详情前保留稳定回焦位置", async () => {
  vi.useFakeTimers();
  try {
    vi.mocked(searchSymbols).mockResolvedValue([item("600127", "金健米业"), item("600825", "新华传媒")]);
    render(<SearchBox />);
    const input = typeQuery("600");
    input.focus();
    await act(async () => { vi.advanceTimersByTime(250); });
    fireEvent.keyDown(input, {key: "ArrowDown"});
    const first = screen.getByRole("button", {name: /金健米业600127/});
    const second = screen.getByRole("button", {name: /新华传媒600825/});
    expect(document.activeElement).toBe(first);
    fireEvent.keyDown(first, {key: "ArrowDown"});
    expect(document.activeElement).toBe(second);
    fireEvent.keyDown(second, {key: "ArrowUp"});
    expect(document.activeElement).toBe(first);
    fireEvent.click(second);
    expect(document.activeElement).toBe(input);
    expect(screen.queryByRole("region", {name: "证券搜索结果"})).toBeNull();
  } finally { vi.useRealTimers(); }
});

describe("SearchBox 有界本地成功选择历史", () => {
  const key = "ashare-security-search-history-v1";
  beforeEach(() => {localStorage.removeItem(key); vi.useFakeTimers();});
  afterEach(() => {vi.useRealTimers(); vi.restoreAllMocks(); localStorage.removeItem(key);});
  async function choose(query: string, symbol: string, name: string) {
    vi.mocked(searchSymbols).mockResolvedValueOnce([item(symbol, name)]);
    typeQuery(query); await act(async () => {vi.advanceTimersByTime(250);});
    fireEvent.click(screen.getByRole("button", {name: new RegExp(`${name}${symbol}`)}));
  }
  it("只在有效结果选中后保存，重挂载后一键详情零重搜且焦点稳定", async () => {
    const view = render(<SearchBox/>); await act(async () => {});
    await choose("guizhoumaotai", "600519", "贵州茅台");
    expect(JSON.parse(localStorage.getItem(key)!)).toEqual([{query: "guizhoumaotai", symbol: "600519", name: "贵州茅台", market: "SH"}]);
    view.unmount(); render(<SearchBox/>); await act(async () => {});
    const input = screen.getByRole("textbox"); fireEvent.focus(input);
    const history = screen.getByRole("button", {name: /查看 贵州茅台/});
    expect(searchSymbols).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(input, {key: "ArrowDown"}); expect(document.activeElement).toBe(history);
    fireEvent.keyDown(history, {key: "ArrowUp"}); expect(document.activeElement).toBe(input);
    detail.open.mockClear(); fireEvent.click(history);
    expect(document.activeElement).toBe(input); await act(async () => {vi.advanceTimersByTime(500);});
    expect(searchSymbols).toHaveBeenCalledTimes(1);
    expect(detail.open).toHaveBeenCalledExactlyOnceWith({symbol: "600519"});
    expect(screen.queryByRole("region", {name: "证券搜索结果"})).toBeNull();
    expect(JSON.parse(localStorage.getItem(key)!)).toHaveLength(1);
  });
  it("同代码SH指数与SZ个股分别直达，历史去重保留市场身份", async () => {
    localStorage.setItem(key, JSON.stringify([
      {query: "shangzheng", symbol: "000001", name: "上证指数", market: "SH"},
      {query: "pingan", symbol: "000001", name: "平安银行", market: "SZ"},
    ]));
    render(<SearchBox/>); await act(async () => {}); const input = screen.getByRole("textbox"); fireEvent.focus(input);
    fireEvent.click(screen.getByRole("button", {name: /查看 上证指数/}));
    expect(detail.open).toHaveBeenLastCalledWith({symbol: "sh000001"});
    fireEvent.focus(input);
    fireEvent.click(screen.getByRole("button", {name: /查看 平安银行/}));
    expect(detail.open).toHaveBeenLastCalledWith({symbol: "000001"});
    await act(async () => {vi.advanceTimersByTime(500);});
    expect(searchSymbols).not.toHaveBeenCalled();
    expect(JSON.parse(localStorage.getItem(key)!)).toHaveLength(2);
  });
  it("显式北交所指数市场不能被通用上证前缀回退覆盖", async () => {
    localStorage.setItem(key, JSON.stringify([{query: "北证50", symbol: "899050", name: "北证50", market: "BJ"}]));
    render(<SearchBox/>); await act(async () => {}); fireEvent.focus(screen.getByRole("textbox"));
    fireEvent.click(screen.getByRole("button", {name: /查看 北证50/}));
    expect(detail.open).toHaveBeenCalledExactlyOnceWith({symbol: "bj899050"});
    expect(searchSymbols).not.toHaveBeenCalled();
  });
  it("已知代码段与市场冲突或未知段只保留检索词，不构成直接历史身份", async () => {
    localStorage.setItem(key, JSON.stringify([
      {query: "wrong-bj", symbol: "600519", name: "错误市场一", market: "BJ"},
      {query: "wrong-sh", symbol: "920025", name: "错误市场二", market: "SH"},
      {query: "wrong-prefix", symbol: "sh600519", name: "前缀冲突", market: "BJ"},
      {query: "unknown-bond", symbol: "113001", name: "未知品种", market: "SH"},
    ]));
    render(<SearchBox/>); await act(async () => {}); fireEvent.focus(screen.getByRole("textbox"));
    expect(screen.queryByRole("button", {name: /^查看/})).toBeNull();
    expect(screen.getAllByRole("button", {name: /^检索词/})).toHaveLength(4);
    vi.mocked(searchSymbols).mockResolvedValueOnce([]);
    fireEvent.click(screen.getByRole("button", {name: "检索词 wrong-bj"}));
    await act(async () => {vi.advanceTimersByTime(250);});
    expect(searchSymbols).toHaveBeenCalledExactlyOnceWith("wrong-bj");
    expect(detail.open).not.toHaveBeenCalled();
    expect(JSON.parse(localStorage.getItem(key)!)).toHaveLength(4);
  });
  it.each([
    ["600519", "BJ"], ["920025", "SH"], ["113001", "SH"],
  ])("当前结果%s/%s身份冲突或未核不能直接开或保存成确定ID", async (symbol, market) => {
    render(<SearchBox/>); await act(async () => {});
    vi.mocked(searchSymbols).mockResolvedValueOnce([{...item(symbol, "待核证券"), market}]);
    typeQuery("check-market"); await act(async () => {vi.advanceTimersByTime(250);});
    fireEvent.click(screen.getByRole("button", {name: new RegExp(`待核证券${symbol}${market}`)}));
    await act(async () => {vi.advanceTimersByTime(500);});
    expect(detail.open).not.toHaveBeenCalled();
    expect(screen.getByRole("alert").textContent).toContain("证券身份待核");
    expect(searchSymbols).toHaveBeenCalledTimes(1);
    expect(JSON.parse(localStorage.getItem(key)!)).toEqual([{query: "check-market"}]);
  });
  it("已核A股ETF与B股代码段保留原市场，均可直接查看", async () => {
    const pairs = [["600519", "SH"], ["510300", "SH"], ["900901", "SH"], ["000001", "SZ"], ["200002", "SZ"], ["300750", "SZ"], ["430047", "BJ"], ["835185", "BJ"], ["920025", "BJ"]];
    localStorage.setItem(key, JSON.stringify(pairs.map(([symbol, market]) => ({query: symbol, symbol, name: symbol, market}))));
    render(<SearchBox/>); await act(async () => {}); const input = screen.getByRole("textbox");
    for (const [symbol, market] of pairs) {
      fireEvent.focus(input); fireEvent.click(screen.getByRole("button", {name: `查看 ${symbol} ${symbol} ${market}`}));
      expect(detail.open).toHaveBeenLastCalledWith({symbol});
    }
    expect(searchSymbols).not.toHaveBeenCalled();
    expect(JSON.parse(localStorage.getItem(key)!)).toHaveLength(9);
  });
  it("SZ1段证券沿原详情入口查看并可从已选历史直接再开", async () => {
    render(<SearchBox/>); await act(async () => {});
    vi.mocked(searchSymbols).mockResolvedValueOnce([{...item("159915", "创业板ETF"), market: "SZ"}]);
    const input = typeQuery("cybetf"); await act(async () => {vi.advanceTimersByTime(250);});
    fireEvent.click(screen.getByRole("button", {name: /创业板ETF159915SZ/}));
    expect(detail.open).toHaveBeenCalledExactlyOnceWith({symbol: "159915"});
    expect(JSON.parse(localStorage.getItem(key)!)[0]).toMatchObject({symbol: "159915", market: "SZ"});
    fireEvent.focus(input); fireEvent.click(screen.getByRole("button", {name: /查看 创业板ETF/}));
    expect(detail.open).toHaveBeenCalledTimes(2); expect(detail.open).toHaveBeenLastCalledWith({symbol: "159915"});
    expect(searchSymbols).toHaveBeenCalledTimes(1);
  });
  it("指数不显示加自选或借裸码的已自选状态", async () => {
    vi.mocked(searchSymbols).mockResolvedValueOnce([{...item("000001", "上证指数"), market: "SH", is_realtime: true}]);
    render(<SearchBox/>); typeQuery("szzs"); await act(async () => {vi.advanceTimersByTime(250);});
    const row = screen.getByRole("button", {name: /上证指数000001SH/}).closest(".search-result-row")!;
    expect(within(row as HTMLElement).getByText("指数 · 仅查看")).toBeTruthy();
    expect(within(row as HTMLElement).queryByText("已自选 ✓")).toBeNull();
    expect(screen.queryByRole("button", {name: /加入自选/})).toBeNull();
    expect(addToWatchlist).not.toHaveBeenCalled();
  });
  it("同批加入SZ000001不把SH000001指数标成已自选", async () => {
    vi.mocked(searchSymbols).mockResolvedValueOnce([{...item("000001", "上证指数"), market: "SH"}, {...item("000001", "平安银行"), market: "SZ"}]);
    vi.mocked(addToWatchlist).mockResolvedValueOnce({symbol: "000001", name: "平安银行", source: "manual", note: null, group_name: "默认", created_at: null, updated_at: null});
    render(<SearchBox/>); typeQuery("000001"); await act(async () => {vi.advanceTimersByTime(250);});
    await act(async () => {fireEvent.click(screen.getByRole("button", {name: "加入自选 平安银行"}));});
    expect(addToWatchlist).toHaveBeenCalledExactlyOnceWith("000001", "平安银行");
    const indexRow = screen.getByRole("button", {name: /上证指数000001SH/}).closest(".search-result-row")!;
    const stockRow = screen.getByRole("button", {name: /平安银行000001SZ/}).closest(".search-result-row")!;
    expect(within(indexRow as HTMLElement).getByText("指数 · 仅查看")).toBeTruthy();
    expect(within(indexRow as HTMLElement).queryByText("已自选 ✓")).toBeNull();
    expect(within(stockRow as HTMLElement).getByText("已自选 ✓")).toBeTruthy();
  });
  it("市场冲突或未知身份结果没有可能写错证券的快捷加入动作", async () => {
    vi.mocked(searchSymbols).mockResolvedValueOnce([{...item("600519", "错市场"), market: "BJ"}, {...item("113001", "未知段"), market: "SH"}]);
    render(<SearchBox/>); typeQuery("identity"); await act(async () => {vi.advanceTimersByTime(250);});
    expect(screen.queryByRole("button", {name: /加入自选/})).toBeNull();
    expect(screen.getAllByText("身份待核")).toHaveLength(2);
    expect(addToWatchlist).not.toHaveBeenCalled();
  });
  it("正常结果与历史共用选中处理，指数显式市场不丢失", async () => {
    render(<SearchBox/>); await act(async () => {});
    vi.mocked(searchSymbols).mockResolvedValueOnce([{...item("000001", "上证指数"), market: "SH"}]);
    typeQuery("szzs"); await act(async () => {vi.advanceTimersByTime(250);});
    fireEvent.click(screen.getByRole("button", {name: /上证指数000001SH/}));
    expect(detail.open).toHaveBeenCalledExactlyOnceWith({symbol: "sh000001"});
    expect(JSON.parse(localStorage.getItem(key)!)[0]).toMatchObject({symbol: "sh000001", market: "SH", query: "szzs"});
  });
  it("旧纯检索词保留检索语义，不按代码或名称猜详情身份", async () => {
    localStorage.setItem(key, JSON.stringify(["000001", {query: "贵州茅台"}, {query: "gmt", symbol: "600519", name: "贵州茅台", market: "SH"}]));
    render(<SearchBox/>); await act(async () => {}); fireEvent.focus(screen.getByRole("textbox"));
    expect(screen.getByRole("button", {name: "检索词 贵州茅台"})).toBeTruthy();
    vi.mocked(searchSymbols).mockResolvedValueOnce([item("000001", "平安银行")]);
    fireEvent.click(screen.getByRole("button", {name: "检索词 000001"}));
    expect(detail.open).not.toHaveBeenCalled();
    await act(async () => {vi.advanceTimersByTime(250);});
    expect(searchSymbols).toHaveBeenCalledExactlyOnceWith("000001");
    expect(detail.open).not.toHaveBeenCalled();
    expect(JSON.parse(localStorage.getItem(key)!)).toHaveLength(3);
  });
  it("直接历史使在途旧搜索失效，不被晚包重新打开", async () => {
    localStorage.setItem(key, JSON.stringify([{query: "gmt", symbol: "600519", name: "贵州茅台", market: "SH"}]));
    let finish!: (items: SymbolSearchItem[]) => void;
    vi.mocked(searchSymbols).mockImplementationOnce(() => new Promise(resolve => {finish = resolve;}));
    render(<SearchBox/>); await act(async () => {});
    const input = typeQuery("old-query"); await act(async () => {vi.advanceTimersByTime(250);});
    fireEvent.change(input, {target: {value: ""}}); fireEvent.focus(input);
    fireEvent.click(screen.getByRole("button", {name: /查看 贵州茅台/}));
    await act(async () => {finish([item("600127", "迟到结果")]); vi.advanceTimersByTime(500);});
    expect(detail.open).toHaveBeenCalledExactlyOnceWith({symbol: "600519"});
    expect(searchSymbols).toHaveBeenCalledTimes(1);
    expect(screen.queryByText("迟到结果")).toBeNull();
    expect(screen.queryByRole("region", {name: "证券搜索结果"})).toBeNull();
  });
  it("成功记录最多12条且按证券去重移到最近，未命中和输入片段不留历史", async () => {
    render(<SearchBox/>); await act(async () => {});
    for (let n = 0; n < 14; n++) await choose(`kw${n}`, String(600000 + n), `成员${n}`);
    let saved = JSON.parse(localStorage.getItem(key)!);
    expect(saved).toHaveLength(12); expect(saved[0].query).toBe("kw13"); expect(saved.at(-1).query).toBe("kw2");
    await choose("quanjiancheng", "600005", "成员5"); saved = JSON.parse(localStorage.getItem(key)!);
    expect(saved).toHaveLength(12); expect(saved[0].query).toBe("quanjiancheng");
    expect(saved.filter((entry: {symbol: string}) => entry.symbol === "600005")).toHaveLength(1);
    vi.mocked(searchSymbols).mockResolvedValueOnce([]); typeQuery("zz_no_hit");
    await act(async () => {vi.advanceTimersByTime(250);});
    expect(JSON.parse(localStorage.getItem(key)!)).toEqual(saved);
  });
  it("坏存储、超长或无效身份不显示为历史；storage拒绝不阻断正常选择", async () => {
    localStorage.setItem(key, JSON.stringify([{query: "x".repeat(100), symbol: "600519", market: "SH"},
      {query: "bad", symbol: "not-a-symbol", market: "SH"}, {query: "gmt", symbol: "600519", name: "贵州茅台", market: "SH"}]));
    const view = render(<SearchBox/>); await act(async () => {}); fireEvent.focus(screen.getByRole("textbox"));
    expect(screen.getAllByRole("button", {name: /查看/})).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", {name: "清除搜索历史"}));
    expect(localStorage.getItem(key)).toBeNull();
    view.unmount();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {throw new Error("storage unavailable");});
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {throw new Error("storage unavailable");});
    render(<SearchBox/>); await act(async () => {}); await choose("gzmts", "600519", "贵州茅台");
    expect(screen.queryByRole("region", {name: "证券搜索结果"})).toBeNull();
  });
  it("历史不抢IME的Enter/Escape，未完成组合不发请求或新增记录", async () => {
    const saved = [{query: "gmt", symbol: "600519", name: "贵州茅台", market: "SH"}]; localStorage.setItem(key, JSON.stringify(saved));
    render(<SearchBox/>); await act(async () => {}); const input = screen.getByRole("textbox"); fireEvent.focus(input);
    fireEvent.compositionStart(input); fireEvent.change(input, {target: {value: "guizhou"}});
    fireEvent.keyDown(input, {key: "Enter"}); fireEvent.keyDown(input, {key: "Escape"});
    await act(async () => {vi.advanceTimersByTime(500);});
    expect(searchSymbols).not.toHaveBeenCalled(); expect(JSON.parse(localStorage.getItem(key)!)).toEqual(saved);
  });
  it.each(["not-json", "{}", "x".repeat(16_385)])("异常历史输入不生成可操作记录", async value => {
    localStorage.setItem(key, value); render(<SearchBox/>); await act(async () => {});
    fireEvent.focus(screen.getByRole("textbox"));
    expect(screen.queryByRole("button", {name: /查看|检索词/})).toBeNull(); expect(searchSymbols).not.toHaveBeenCalled();
  });
});
