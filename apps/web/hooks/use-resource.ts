"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { isTradingSession } from "@/lib/market-hours";

/**
 * 前端取数的统一抽象（S2-5）。取代「各页自己 `void load(); setInterval(load, ms)`」的样板，
 * 并把三件全站性的门控**内建在唯一入口**里，而不是要求 40 个调用点各写一遍：
 *
 * 1. **可见性暂停**：`document.hidden` 期间不轮询；回可见时**立即补拉一次**再恢复节奏。
 *    背景标签页不再对后端与上游配额空转（P0-1）。
 * 2. **盘外降频**：非交易时段把基础间隔 ×5，且**封顶 120s**
 *    （封顶是为了把「开盘瞬间的追赶延迟」限制在 2 分钟内——不封顶时 60s 轮询
 *    在盘外变 300s，09:00 打开的页面可能到 09:20 才切回盘中节奏）。
 *    时段判定复用 `lib/market-hours.ts` 的粗判（宽松区间，节假日误判为盘中只是多打一次
 *    后端缓存，无害）。非行情类取数（任务中心 / 通知 / 助手）传 `marketHours: false` 关掉。
 * 3. **三态**：`status` 显式区分 `unknown`（未判定）/ `pending` / `ready` / `error`。
 *    失败**保留上一次数据**（`data` 不清空、`status` 仍为 `ready`，同时 `error` 非空），
 *    与各页既有「失败保持旧值 + 各自空态」的约定一致；只有「从未成功过」才是 `error`。
 * 4. **结果与 key 绑定 + 单飞**（R20，2026-09-14）：
 *    - **绑定**：`data` / `error` 只在「产生它的 key === 当前 key」时才对外可见，
 *      否则回落到 `pending`（失败则 `error`）。改 key 后**绝不用旧 key 的值顶替新 key**
 *      （旧实现在 B 在途/失败时报 A 的数据，且三态被抹成 `ready`）。
 *      ⚠️ 这条**只影响直接消费 `useResource` 返回值的调用点**：薄壳 `usePollingFetch`
 *      "调用即忘"，读不到 `data`，行为不变。
 *    - **单飞**：同一 effect 实例内任何时刻最多一个在途请求。回可见时若已有请求在途，
 *      合并而非再发一次 —— 旧实现会并发两次，并因此排出**两条计时链**，
 *      之后每周期取数翻倍（实测 2→4→6）。
 *    刻意**不做网络层 abort**：那需要给 `fn` 加 `AbortSignal` 形参并改动 45 个调用点
 *    的 `fetch` 调用；而"丢弃过期回包"（逻辑取消）已足以保证正确性，
 *    网络层取消只省带宽、不改结论，属另一件事（YAGNI）。
 *
 *    ⚠️ **约束：`fn` 必须返回值，三态才成立**（2026-09-12 评审批次 2，有测试钉住）。
 *    `ready` 的判据是 `data !== undefined` —— 若 `fn` 返回 `undefined`（含调用方写
 *    "调用即忘"的 `fn: () => { setX(...) }` 而**不 return**），`status` 会**永远停在
 *    `pending`**：不报错、不告警、`error` 也正常，只是三态静默失去意义。
 *    全站 45 个调用点走的薄壳 `usePollingFetch` 恰是这种形态（它**丢弃**返回值），
 *    故**它不适用三态**——需要三态请直接调 `useResource` 并让 `fn` 返回数据。
 *    这是刻意取舍：把三态挂在返回值上，比让 hook 去猜「调用方是否已自行 setState」可靠。
 *    （门控三件套与三态无关，45 处照常消费。）
 *
 * 其余契约与 `usePollingFetch` 相同：
 * - `fn` 经 latest-ref 间接调用，调用方无须把它挂进依赖；闭包捕获的参数每次渲染取最新值。
 * - `intervalMs = null` 表示只挂载拉一次（如依赖 URL 参数的初始拉取）。
 * - `key`（参数会变的取数必须传）：变化时**立即重拉**，否则只会等下一次轮询，是静默漏刷新。
 * - `enabled = false` 表示条件轮询（如「该页签激活才拉」），关闭时不发任何请求。
 *
 * 实现要点：轮询用 **setTimeout 链**而非 setInterval —— 间隔每轮重算，因此
 * 「盘外 → 盘中」的切换能在下一拍自动生效，不需要额外的定时器去盯时段边界。
 * 所有 setState 都发生在 promise 回调 / 定时器回调 / 事件回调里，不在 effect 同步路径上
 * （react-hooks/set-state-in-effect 推荐的结构）。
 */

/** 取数三态 + 一态显式「未判定」（对应后端 Freshness 契约的 `missing_reason` 口径）。 */
export type ResourceStatus = "unknown" | "pending" | "ready" | "error";

export interface Resource<T> {
  /** 最近一次成功取数的结果；从未成功过为 `undefined`。失败时**不清空**。 */
  data: T | undefined;
  /** 首次加载 / 手动 refresh 进行中。后台轮询**不**置位（避免每次轮询闪一下 loading）。 */
  pending: boolean;
  /** 最近一次失败；成功后清空。与 `data` 并存时表示「有旧值但刷新失败」。 */
  error: unknown;
  /** 三态派生值。 */
  status: ResourceStatus;
  /** 手动立即重拉（不重置已有 data，避免闪空）。 */
  refresh: () => void;
}

export interface ResourceOptions {
  intervalMs?: number | null;
  key?: unknown;
  /** 条件轮询开关，默认 true。 */
  enabled?: boolean;
  /** 是否套用「盘外 ×5 降频」，默认 true（行情类）。非行情类取数传 false。 */
  marketHours?: boolean;
}

/** 盘外降频倍数。 */
const OFF_SESSION_FACTOR = 5;
/** 盘外间隔封顶：把开盘瞬间的追赶延迟限制在 2 分钟内（见文件头说明）。 */
const OFF_SESSION_MAX_MS = 120_000;

function isHidden(): boolean {
  // 非 "hidden"（含 SSR 无 document、以及 "prerender"）一律按可见处理：
  // 宁可多拉一次，也不要在拿不准的时候把页面冻住。
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

function nextDelay(base: number | null, marketHours: boolean): number | null {
  if (base == null) return null;
  if (!marketHours || isTradingSession()) return base;
  return Math.min(base * OFF_SESSION_FACTOR, Math.max(base, OFF_SESSION_MAX_MS));
}

export function useResource<T = unknown>(fn: () => Promise<T>, options: ResourceOptions = {}): Resource<T> {
  const { intervalMs = null, key, enabled = true, marketHours = true } = options;

  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  });

  /**
   * 取数结果快照 —— **与产生它的 `key` 绑在一起**（R20）。
   *
   * 缺陷：原实现把 `data` / `error` 与 `key` 各存各的，`key` 从 A 变 B 时只重跑请求、
   * **不清旧值** ⇒ B 的请求还在途（甚至已经失败）时，对外报的仍是 A 的数据/结论：
   * 实测 `status` 是 `ready`（拿 A 的值顶替 B），失败时同样是 `ready` 而非 `error`
   * —— 界面把 A 的东西挂在 B 的标题下，且三态被这个"顶替"抹掉。
   * 现语义：结果必须自证"我是哪个 key 的"，读取端只在快照 key 与当前 key 一致时才认。
   * 不一致 = 新 key 尚未就绪（`pending` / 失败则 `error`），**绝不拿旧 key 的值充数**。
   *
   * 用 `Object.is` 比对，与 React 判定 effect 依赖（即 `key` 变化）的口径保持一致，
   * 否则会出现"effect 已重跑、快照却仍被认作有效"的不一致窗口。
   */
  const [snap, setSnap] = useState<{ key: unknown; data: T | undefined; error: unknown }>(() => ({
    key,
    data: undefined,
    error: null,
  }));
  // 手动 refresh：自增 nonce 重跑 effect；refreshing 只由 refresh()（事件上下文）置位
  const [refreshing, setRefreshing] = useState(false);
  const [nonce, setNonce] = useState(0);

  const refresh = useCallback(() => {
    // enabled=false 时**必须是 no-op**（2026-09-12 评审批次 2）：
    // 下面 effect 的 `if (!enabled) return;` 会提前返回，`refreshing` 再也无人清回 false
    // ⇒ 由于 `pending = refreshing || status === "pending"`，一次误调就会让 `pending`
    // **永久卡在 true**（实测确认）。关闭态本就不该发请求，直接返回语义也更正确。
    if (!enabled) return;
    setRefreshing(true);
    setNonce((n) => n + 1);
  }, [enabled]);

  useEffect(() => {
    if (!enabled) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | null = null;
    /** 在途请求（**单飞**，R20）。同一 effect 实例内任何时刻最多一个。 */
    let inFlight: Promise<void> | null = null;

    /** ⚠️ `key` 在本 effect 实例内是**常量**：key 变化会重跑 effect（它在依赖里），
     *  旧实例的 `alive` 随即为 false、其回包被丢弃。故 `run()` 里可以放心把
     *  捕获到的 `key` 当作"本次取数的身份"写进快照。 */
    const dataKey = key;

    const clear = () => {
      if (timer !== null) {
        clearTimeout(timer);
        timer = null;
      }
    };

    const run = (): Promise<void> => {
      if (!alive) return Promise.resolve();
      // **单飞**：已有在途请求时复用同一个 promise，不再发第二个请求。
      // 为什么必须有：`onVisibility`（回可见）与定时器回调可能在同一次取数在途期间
      // 先后触发；旧实现两处都直接 `run()` ⇒ 并发两次请求，且两条 `run().then(schedule)`
      // 各排一个定时器，计时链就此叠加（实测"每周期 2 次"，并逐轮累积成 2→4→6）。
      if (inFlight) return inFlight;
      const p = (async () => {
        try {
          const next = await fnRef.current();
          if (!alive) return;
          setSnap({ key: dataKey, data: next, error: null });
        } catch (e) {
          if (!alive) return;
          // 保留 data：**同一 key 内**失败不清空旧值（既有契约）；
          // 但 data 只在它也属于本 key 时才留 —— 否则就是拿别的 key 的值充数。
          setSnap((s) => ({
            key: dataKey,
            data: Object.is(s.key, dataKey) ? s.data : undefined,
            error: e,
          }));
        } finally {
          if (alive) setRefreshing(false);
          inFlight = null;
        }
      })();
      inFlight = p;
      return p;
    };

    /** 排定下一拍。**幂等**：先清掉已有计时器 —— 即便定时器回调与 visibilitychange
     *  回调先后收尾于同一次取数（两者都会走到这里），最终也只剩**一条**计时链。 */
    const schedule = () => {
      clear();
      const delay = nextDelay(intervalMs, marketHours);
      if (delay == null || !alive) return;
      timer = setTimeout(() => {
        timer = null;
        if (!alive) return;
        if (isHidden()) return; // 隐藏期不拉，等 visibilitychange 补
        void tick();
      }, delay);
    };

    /** 取一次 + 重排下一拍。所有触发源（首挂载 / 定时器 / 回可见）都只经由此处，
     *  于是"排定下一拍"这件事在全模块只有一个出口。 */
    const tick = async () => {
      await run();
      if (alive) schedule();
    };

    const onVisibility = () => {
      if (!alive) return;
      if (isHidden()) {
        clear(); // 隐藏：停表
        return;
      }
      clear();
      void tick(); // 回可见：立即补拉一次（在途则合并），再重排
    };

    void tick();
    if (typeof document !== "undefined") document.addEventListener("visibilitychange", onVisibility);

    return () => {
      alive = false;
      clear();
      if (typeof document !== "undefined") document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [enabled, intervalMs, key, marketHours, nonce]);

  // 快照只在"属于当前 key"时才算数；否则一律回到未判定（三态优先于"有值就显示"）。
  const matched = Object.is(snap.key, key);
  const data = matched ? snap.data : undefined;
  const error = matched ? snap.error : null;

  const status: ResourceStatus = !enabled
    ? "unknown"
    : data !== undefined
      ? "ready"
      : error != null
        ? "error"
        : "pending";

  return { data, pending: refreshing || status === "pending", error, status, refresh };
}
