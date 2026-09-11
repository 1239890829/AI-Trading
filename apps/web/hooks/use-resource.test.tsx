import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useResource } from "@/hooks/use-resource";

// 时段判定是**外部**输入：这里只验证「非交易时段 → 降频」的契约，
// 时段本身的边界算法由 lib/market-hours 自己负责。
const trading = vi.hoisted(() => ({ value: true }));
vi.mock("@/lib/market-hours", () => ({
  isTradingSession: () => trading.value,
}));

/** 改写 document.visibilityState 并派发 visibilitychange。 */
function setVisibility(state: "visible" | "hidden") {
  Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
  document.dispatchEvent(new Event("visibilitychange"));
}

/** 让挂起的 promise 结算（含 effect 内 `run().then(schedule)` 的排定）。 */
async function flush() {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });
}

/** 推进定时器并结算沿途的 promise。 */
async function advance(ms: number) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  trading.value = true;
  setVisibility("visible");
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useResource", () => {
  it("挂载即拉取一次，并把三态从 pending 推到 ready", async () => {
    const fn = vi.fn(async () => "v1");
    const { result } = renderHook(() => useResource(fn, { intervalMs: null }));

    // 首拉在 effect 同步路径上发起（fn 立即被调用），但结果尚未结算
    expect(fn).toHaveBeenCalledTimes(1);
    expect(result.current.status).toBe("pending");
    expect(result.current.pending).toBe(true);

    await flush();
    expect(result.current.status).toBe("ready");
    expect(result.current.pending).toBe(false);
    expect(result.current.data).toBe("v1");
    expect(result.current.error).toBeNull();
  });

  it("intervalMs=null 只挂载拉一次，不轮询", async () => {
    const fn = vi.fn(async () => 1);
    renderHook(() => useResource(fn, { intervalMs: null }));
    await flush();

    await advance(10 * 60_000);
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("按 intervalMs 轮询（盘中不加倍）", async () => {
    const fn = vi.fn(async () => 1);
    renderHook(() => useResource(fn, { intervalMs: 10_000 }));
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);

    await advance(10_000);
    expect(fn).toHaveBeenCalledTimes(2);
    await advance(10_000);
    expect(fn).toHaveBeenCalledTimes(3);
  });

  it("盘外降频 ×5", async () => {
    trading.value = false;
    const fn = vi.fn(async () => 1);
    renderHook(() => useResource(fn, { intervalMs: 10_000 }));
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);

    await advance(49_999);
    expect(fn).toHaveBeenCalledTimes(1); // 未到 50s
    await advance(1);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("盘外降频封顶 120s（60s 轮询不会变成 300s）", async () => {
    trading.value = false;
    const fn = vi.fn(async () => 1);
    renderHook(() => useResource(fn, { intervalMs: 60_000 }));
    await flush();

    await advance(119_999);
    expect(fn).toHaveBeenCalledTimes(1);
    await advance(1);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("盘外降频封顶不会让长间隔变快（300s 仍是 300s）", async () => {
    trading.value = false;
    const fn = vi.fn(async () => 1);
    renderHook(() => useResource(fn, { intervalMs: 300_000 }));
    await flush();

    await advance(120_000);
    expect(fn).toHaveBeenCalledTimes(1);
    await advance(180_000);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("marketHours=false 时盘外不降频（通知 / 任务中心 / 助手气泡）", async () => {
    trading.value = false;
    const fn = vi.fn(async () => 1);
    renderHook(() => useResource(fn, { intervalMs: 10_000, marketHours: false }));
    await flush();

    await advance(10_000);
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("页面隐藏时暂停轮询，回可见立即补拉一次并恢复节奏", async () => {
    const fn = vi.fn(async () => 1);
    renderHook(() => useResource(fn, { intervalMs: 10_000 }));
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);

    await act(async () => {
      setVisibility("hidden");
    });
    await advance(60_000);
    expect(fn).toHaveBeenCalledTimes(1); // 隐藏期零请求

    await act(async () => {
      setVisibility("visible");
    });
    await flush();
    expect(fn).toHaveBeenCalledTimes(2); // 回可见立即补拉

    await advance(10_000);
    expect(fn).toHaveBeenCalledTimes(3); // 节奏恢复
  });

  it("失败 → error 态；已有数据时保留旧值不闪空", async () => {
    let fail = false;
    const fn = vi.fn(async () => {
      if (fail) throw new Error("boom");
      return "v1";
    });
    const { result } = renderHook(() => useResource(fn, { intervalMs: 10_000 }));
    await flush();
    expect(result.current.status).toBe("ready");

    fail = true;
    await advance(10_000);
    expect(result.current.data).toBe("v1"); // 旧值保留
    expect(result.current.status).toBe("ready"); // 有数据就是 ready
    expect(result.current.error).toBeInstanceOf(Error); // 失败同时可见
  });

  it("从未成功过时的失败 → status=error（与「未判定」区分）", async () => {
    const fn = vi.fn(async () => {
      throw new Error("boom");
    });
    const { result } = renderHook(() => useResource(fn, { intervalMs: null }));
    await flush();

    expect(result.current.status).toBe("error");
    expect(result.current.data).toBeUndefined();
  });

  it("key 变化 → 立即重拉（不等下一次轮询）", async () => {
    const fn = vi.fn(async () => 1);
    const { rerender } = renderHook(({ k }) => useResource(fn, { intervalMs: 60_000, key: k }), {
      initialProps: { k: "a" },
    });
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);

    rerender({ k: "b" });
    await flush();
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("enabled=false 不发请求，转 true 后立即拉一次", async () => {
    const fn = vi.fn(async () => 1);
    const { rerender, result } = renderHook(({ on }) => useResource(fn, { intervalMs: 10_000, enabled: on }), {
      initialProps: { on: false },
    });
    await flush();
    expect(fn).toHaveBeenCalledTimes(0);
    expect(result.current.status).toBe("unknown"); // 未判定 ≠ 空

    rerender({ on: true });
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);
  });

  it("refresh() 立即重拉且不把已有数据清空", async () => {
    const fn = vi.fn(async () => "v");
    const { result } = renderHook(() => useResource(fn, { intervalMs: 60_000 }));
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);

    // 同步 act：只跑 effect，不结算微任务 ⇒ 能观察到"刷新中"这一帧
    act(() => {
      result.current.refresh();
    });
    expect(fn).toHaveBeenCalledTimes(2); // 立即重拉，不等 60s
    expect(result.current.pending).toBe(true); // 手动刷新有 pending 反馈
    expect(result.current.data).toBe("v"); // 旧值不清空（不闪空）

    await flush();
    expect(result.current.data).toBe("v");
    expect(result.current.pending).toBe(false);
  });

  it("卸载后不再拉取", async () => {
    const fn = vi.fn(async () => 1);
    const { unmount } = renderHook(() => useResource(fn, { intervalMs: 10_000 }));
    await flush();
    expect(fn).toHaveBeenCalledTimes(1);

    unmount();
    await advance(60_000);
    expect(fn).toHaveBeenCalledTimes(1);
  });
});
