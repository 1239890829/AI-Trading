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

  it("【守卫】enabled=false 时 refresh() 是 no-op，不得把 pending 卡在 true", async () => {
    // 2026-09-12 评审批次 2：旧实现无条件 setRefreshing(true)，而 effect 在
    // `if (!enabled) return;` 处提前返回 ⇒ 没有任何路径把 refreshing 清回 false，
    // 于是 `pending` 永久为 true。注入验证：去掉 refresh 里的 `if (!enabled) return;`
    // 本用例应精确变红。
    const fn = vi.fn(async () => 1);
    const { result } = renderHook(() => useResource(fn, { intervalMs: 10_000, enabled: false }));
    await flush();
    expect(result.current.status).toBe("unknown");

    act(() => {
      result.current.refresh();
    });
    await flush();

    expect(fn).toHaveBeenCalledTimes(0); // 关闭态不发请求
    expect(result.current.pending).toBe(false); // 关键：不卡 true
    expect(result.current.status).toBe("unknown");
  });

  it("【约束】三态以「fn 是否返回值」为判据：不返回值 ⇒ 恒 pending（薄壳不适用三态）", async () => {
    // 这条**不是缺陷修复，是把既有取舍钉成契约**（2026-09-12 评审批次 2）。
    // 全站 45 个调用点经薄壳 usePollingFetch 以"调用即忘"形态使用（丢弃返回值），
    // 故它们**读不到三态**。此处用「同样形状、只差一个 return」的两枝并排证明判据来源，
    // 防止后来者误以为「恒 pending」是 bug 而去改 ready 的判定条件（那会波及全部消费方）。
    const withValue = vi.fn(async () => "v");
    const withoutValue = vi.fn(async () => {
      /* 副作用型取数：不返回值，正是薄壳调用方的形态 */
    });

    const a = renderHook(() => useResource(withValue, { intervalMs: null }));
    const b = renderHook(() => useResource(withoutValue, { intervalMs: null }));
    await flush();

    // 同样的调用次数、同样的无错误——差别只在返回值
    expect(withValue).toHaveBeenCalledTimes(1);
    expect(withoutValue).toHaveBeenCalledTimes(1);
    expect(a.result.current.status).toBe("ready");
    expect(b.result.current.status).toBe("pending");
    expect(b.result.current.error).toBeNull();
    expect(b.result.current.pending).toBe(true);
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

  // ── R20：过期响应串数据 / 多重计时链 ────────────────────────────────────────
  // 缺陷一：`data` 与 `error` **未与 key 绑定** ⇒ key 从 A 变 B 后，B 的请求还在途
  // （甚至已失败）时，对外仍报 A 的数据，界面把 A 的东西挂在 B 的标题下。
  // 缺陷二：`onVisibility` 回可见时**无条件再发一次**；若已有请求在途，两条
  // `run().then(schedule)` 各排一个定时器 ⇒ 计时链叠加，之后每周期取数翻倍。

  it("【R20】key 变化后、新请求结算前，不得把旧 key 的数据充当新 key 的结果", async () => {
    let releaseB!: (v: string) => void;
    const pendingB = new Promise<string>((r) => {
      releaseB = r;
    });
    const fn = vi.fn(async (k: string) => (k === "a" ? "A-DATA" : pendingB));
    const { result, rerender } = renderHook(
      ({ k }: { k: string }) => useResource(() => fn(k), { intervalMs: 60_000, key: k }),
      { initialProps: { k: "a" } }
    );
    await flush();
    expect(result.current.data).toBe("A-DATA");
    expect(result.current.status).toBe("ready");

    rerender({ k: "b" });
    await flush(); // B 的请求仍在途
    expect(result.current.status).toBe("pending"); // 未判定 ≠ 拿 A 的值顶替
    expect(result.current.data).toBeUndefined();

    await act(async () => {
      releaseB("B-DATA");
    });
    await flush();
    expect(result.current.data).toBe("B-DATA");
    expect(result.current.status).toBe("ready");
  });

  it("【R20】新 key 失败时 error 与数据都归属新 key，不混入旧 key 的结论", async () => {
    let fail = false;
    const fn = vi.fn(async () => {
      if (fail) throw new Error("boom");
      return "A-DATA";
    });
    const { result, rerender } = renderHook(
      ({ k }: { k: string }) => useResource(fn, { intervalMs: 60_000, key: k }),
      { initialProps: { k: "a" } }
    );
    await flush();
    expect(result.current.data).toBe("A-DATA");

    fail = true;
    rerender({ k: "b" });
    await flush();
    // 旧实现：data 仍是 "A-DATA" ⇒ status 被算成 ready（旧值 + 新错误混作一体）
    expect(result.current.status).toBe("error");
    expect(result.current.data).toBeUndefined();
    expect(result.current.error).toBeInstanceOf(Error);
  });

  it("【R20】回可见时若有请求在途，不得再发一次（单飞）", async () => {
    const gates: Array<(v: number) => void> = [];
    const fn = vi.fn(
      () =>
        new Promise<number>((r) => {
          gates.push(r);
        })
    );
    renderHook(() => useResource(fn, { intervalMs: 10_000 }));
    expect(fn).toHaveBeenCalledTimes(1);

    // 结算首拉 → 轮询链启动
    await act(async () => {
      gates.shift()!(1);
    });
    await flush();

    // 定时器触发 → 第 2 次取数在途
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(fn).toHaveBeenCalledTimes(2);

    // 在途期间 隐藏 → 回可见：应合并在途的那一次，**不得**再发第 3 次
    await act(async () => {
      setVisibility("hidden");
    });
    await act(async () => {
      setVisibility("visible");
    });
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("【R20】回可见与定时器先后收尾于同一次取数时，计时链仍只有一条（每周期 1 次）", async () => {
    // ⚠️ 判据必须让"两条链"可观测：有单飞在，两条链**每周期也只发 1 次请求**
    //（两个计时器各自 tick，后一个被合并）⇒ 只看请求数会漏判。
    // 故在"制造双链"之后把取数切成**同步结算**（hold=false）：此时两个计时器先后
    // 触发时前一次已收尾、单飞不再合并 ⇒ 双链表现为**每周期 2 次**（= 原始症状）。
    let hold = true;
    const gates: Array<(v: number) => void> = [];
    const fn = vi.fn(() =>
      hold
        ? new Promise<number>((r) => {
            gates.push(r);
          })
        : Promise.resolve(1)
    );
    renderHook(() => useResource(fn, { intervalMs: 10_000 }));

    // 结算首拉 → 轮询链启动
    await act(async () => {
      gates.shift()!(1);
    });
    await flush();

    // 定时器触发 → 第 2 次取数在途；期间来回切可见性，让"定时器回调"与
    // "visibilitychange 回调"都将在本次取数收尾时重排下一拍（旧实现各排一个）
    hold = true;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    await act(async () => {
      setVisibility("hidden");
    });
    await act(async () => {
      setVisibility("visible");
    });

    hold = false;
    await act(async () => {
      gates.splice(0).forEach((r) => r(1));
    });
    await flush();

    // 用实得基线核算"每个周期恰好 +1"：双链会 +2（这正是"2→4→6"的由来）
    const settled = fn.mock.calls.length;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(fn.mock.calls.length).toBe(settled + 1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(10_000);
    });
    expect(fn.mock.calls.length).toBe(settled + 2);
  });
});
