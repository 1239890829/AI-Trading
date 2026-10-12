import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { LonghuFlowCard } from "./fund-tab";
import { getLonghu } from "@/lib/api";
vi.mock("@/lib/api", async (original) => ({ ...await original<typeof import("@/lib/api")>(), getLonghu: vi.fn() }));
afterEach(() => { cleanup(); vi.resetAllMocks(); });

describe("龙虎榜回退读取生命周期", () => {
  it("退出后不继续启动下一历史日请求", async () => {
    let fail!: (reason: Error) => void;
    vi.mocked(getLonghu).mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject; }))
      .mockRejectedValue(new Error("offline"));
    const view = render(<LonghuFlowCard fallbackDates={["2026-10-09", "2026-10-08"]} />);
    await waitFor(() => expect(getLonghu).toHaveBeenCalledTimes(1));
    view.unmount();
    await act(async () => fail(new Error("offline")));
    expect(getLonghu).toHaveBeenCalledTimes(1);
  });
  it("同数量日期替换也建立新读取身份，旧失败链不能继续", async () => {
    let fail!: (reason: Error) => void;
    vi.mocked(getLonghu).mockImplementationOnce(() => new Promise((_resolve, reject) => { fail = reject; }))
      .mockResolvedValueOnce([]);
    const view = render(<LonghuFlowCard fallbackDates={["2026-10-08"]} />);
    await waitFor(() => expect(getLonghu).toHaveBeenCalledTimes(1));
    view.rerender(<LonghuFlowCard fallbackDates={["2026-10-09"]} />);
    await waitFor(() => expect(getLonghu).toHaveBeenCalledTimes(2));
    await screen.findByText(/此次读取未返回可计入当日/);
    await act(async () => fail(new Error("offline")));
    expect(getLonghu).toHaveBeenCalledTimes(2);
  });
});
