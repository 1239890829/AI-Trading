"use client";

import { useResource } from "@/hooks/use-resource";

/**
 * 挂载即拉取 + 定时轮询的统一入口（替代散落各页的 `void load(); setInterval(load, ms)` 样板）。
 *
 * **S2-5（2026-09-11）起本体已迁至 `hooks/use-resource.ts`**：可见性暂停、盘外 ×5 降频、
 * 三态返回值都在 `useResource` 里实现。本函数保留为**向后兼容薄壳**——全站 20 余个调用点
 * 写的是「调用即忘」（`fn` 自己 `setState`），改签名会波及面过大且无收益。
 *
 * 新代码请直接用 `useResource`：它能拿到 `{data, status, error, refresh}` 三态，
 * 不必再把数据塞进组件自己的 `useState`（那正是"空池 / 加载中 / 失败"三者不可分的来源）。
 *
 * 参数语义与 `useResource` 一致：
 * - `fn` 经 latest-ref 间接调用：调用方无须把 fn 挂进依赖，fn 闭包捕获的参数
 *   （如 URL date）每次渲染都会被最新一次引用，挂载/轮询始终跑最新版本；
 * - `intervalMs = null` 表示只挂载拉一次（如依赖 URL 参数的初始拉取）；
 * - `key`：**参数会变的取数必须传**。effect 只在 `intervalMs` / `key` 变化时重跑——
 *   若取数依赖 code/date 之类会变的入参而 key 不传，参数变化时不会立即重拉（只会等下一次轮询），
 *   是静默的漏刷新；
 * - `options.marketHours = false`：该取数不是行情（任务中心 / 通知 / 助手气泡），
 *   关掉盘外降频——否则盘后轮询被拉到 120s，用户看不到本该及时出现的提醒；
 * - `options.enabled = false`：条件轮询（如「该页签激活才拉」），关闭时不发任何请求。
 *
 * 卸载后不再触发（alive 哨兵）；调用方自行负责 catch（fn 内部吞错）或在此处
 * 统一吞错（轮询型页面的既有约定：失败保持上一次数据 + 各自空态）。
 */
export function usePollingFetch(
  fn: () => Promise<unknown>,
  intervalMs: number | null,
  key?: unknown,
  options?: { marketHours?: boolean; enabled?: boolean }
) {
  useResource(fn, {
    intervalMs,
    key,
    marketHours: options?.marketHours ?? true,
    enabled: options?.enabled ?? true,
  });
}

export { useResource } from "@/hooks/use-resource";
export type { Resource, ResourceOptions, ResourceStatus } from "@/hooks/use-resource";
