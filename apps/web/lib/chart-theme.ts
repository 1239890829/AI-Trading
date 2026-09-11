"use client";

import { useSyncExternalStore } from "react";

/**
 * Canvas 图表的主题感知（P2-26，2026-09-11）。
 *
 * ## 为什么需要它
 * lightweight-charts 把颜色**写在 series 的 options 里**（`addLineSeries({ color })`），
 * 它不认识 CSS 变量、也不认识 Tailwind 的 `dark:` —— 那些只作用于 DOM 元素。
 * Tailwind 的 `dark:` 档在**构建时**被编译成 `.dark .xxx { … }` 选择器，只对 DOM 生效；
 * canvas 里的像素是一次性绘制的，主题切换不会让它重画。
 *
 * 后果（亮色模式实测）：MA5 画线 `#facc15` 在浅色卡片上只有 **1.47:1**，线基本看不见。
 * 而**文本对比度扫描器看不见 canvas**，所以整轮对比度专项（P2-19/P2-24）一路报 0 违规，
 * 图标与画线这类非文本面完全在雷达之外（同 P2-27）。
 *
 * ## 设计
 * 主题在本项目是 `documentElement` 上的 `dark` class（`app/layout.tsx` 的 inline script
 * 在 hydration 前写入，`nav-bar.tsx` 切换），**没有** React context。故用
 * `useSyncExternalStore` 订阅该 class 的变化：组件因此重渲染，进而把新配色
 * `applyOptions` 到既有 series 上。
 *
 * ⚠️ **只换色、不重建图表**。重建会丢掉用户的缩放与平移位置（图表已有
 * 「创建与填充分离」的既有约定，见 `kline-chart-pro.tsx` 顶部注释）。
 */

export type ChartTheme = "light" | "dark";

/**
 * 同步读当前主题（**只能在 effect / 事件处理器里调用**，渲染期读 DOM 会破坏 SSR）。
 * 图表的创建 effect 用它拿首建配色：此时 DOM 上的 class 已是权威值，
 * 不受 `useSyncExternalStore` 在 hydration 首帧用 server snapshot（"dark"）的影响。
 */
export function readChartTheme(): ChartTheme {
  if (typeof document === "undefined") return "dark";
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

function subscribe(onChange: () => void): () => void {
  if (typeof document === "undefined") return () => {};
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}

function getServerTheme(): ChartTheme {
  // 与 `app/layout.tsx` 的 inline script 默认一致（`t !== "light"` ⇒ 默认深色），
  // 使 hydration 首帧不产生"亮→暗"的可见跳变。
  return "dark";
}

/**
 * 订阅当前主题。返回值变化 = 需要把新配色应用到已建的 series 上。
 *
 * 服务端快照固定为 `"dark"`（与首屏 inline script 的默认一致）；
 * hydration 后 `useSyncExternalStore` 会立即用真实值校正。
 */
export function useChartTheme(): ChartTheme {
  return useSyncExternalStore(subscribe, readChartTheme, getServerTheme);
}
