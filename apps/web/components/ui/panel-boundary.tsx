"use client";

import { Component, type ErrorInfo, type ReactNode } from "react";

/**
 * 组件级错误边界（S2-6，2026-09-11）。
 *
 * ## 为什么需要
 * 此前全仓只有**路由级**边界（`app/error.tsx` / `app/global-error.tsx`）。
 * 路由边界的粒度是整页：任何一个面板在渲染期抛错（最常见的形态是"接口 200 但
 * 响应体不是预期结构"→ 取 `.length` 崩），**整页都会被替换成那张错误卡**，
 * 用户丢掉的是全部已经加载好的内容，只为看一个面板的失败。
 *
 * 本组件把降级收敛到**出错的那一块**：其余面板照常可用，失败块就地给出一条
 * 可读的错误信息 + 重试入口。
 *
 * ## 用法（优先挂 `Panel`，见 `components/panel.tsx` 的集成）
 * ```tsx
 * <PanelBoundary label="题材梯队" resetKey={themeCode}>
 *   <ThemeLadder ... />
 * </PanelBoundary>
 * ```
 *
 * ## `resetKey` 不是可选项
 * React 的错误边界一旦进入错误态，**不会**因为 children 变了就自动恢复。
 * 详情面板这类"同一块位置反复换内容"的场景（切标的、切 tab）必须给 `resetKey`：
 * 值一变就清掉错误态重新渲染，否则一只票渲染失败会把这个面板钉死在错误卡上，
 * 直到用户整页刷新——把"局部降级"又变成了"整页不可用"。
 *
 * ## 边界能抓到什么、抓不到什么
 * - **能**：子树**渲染期**抛出的同步异常（含 hooks 执行期、render 内计算）。
 * - **不能**：事件处理器里的异常（React 不把它们冒泡给边界）、异步回调
 *   （`setTimeout` / promise `.then`）里的异常、SSR 期间服务端的异常。
 *   这三类由各自的取数层降级（`useResource` 的三态）与根边界兜底，不要指望这里。
 */
interface PanelBoundaryProps {
  children: ReactNode;
  /** 出错时点名的对象（面板标题）。纯文本——它要进 `console.error` 与文案。 */
  label?: string;
  className?: string;
  /** 值变化时清除错误态。切标的/切 tab 的场景必须传。 */
  resetKey?: unknown;
  /** 额外上报（埋点/告警）。默认行为已经 `console.error`，不需要重复打印。 */
  onError?: (error: Error, info: ErrorInfo) => void;
}

interface PanelBoundaryState {
  error: Error | null;
}

export class PanelBoundary extends Component<PanelBoundaryProps, PanelBoundaryState> {
  state: PanelBoundaryState = { error: null };

  static getDerivedStateFromError(error: Error): PanelBoundaryState {
    return { error };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    // 必须留痕：组件级降级把错误"吃掉"了，不留痕就成了静默失败——
    // 用户只看到一块灰卡，开发者连出过错都不知道。
    console.error(`[PanelBoundary] ${this.props.label ?? "面板"} 渲染失败`, error, info.componentStack);
    this.props.onError?.(error, info);
  }

  componentDidUpdate(prev: PanelBoundaryProps) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) {
      this.setState({ error: null });
    }
  }

  private retry = () => this.setState({ error: null });

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;

    return (
      <div
        role="alert"
        className={
          "flex flex-col items-start gap-2 rounded-lg border border-dashed border-amber-500/50 bg-amber-500/[0.04] p-3 text-left " +
          (this.props.className ?? "")
        }
      >
        <div className="text-xs font-medium text-amber-800 dark:text-amber-300">
          {this.props.label ? `${this.props.label} · 局部加载失败` : "局部加载失败"}
        </div>
        <p className="w-full break-all text-[11px] leading-5 text-zinc-600 dark:text-zinc-400">
          {error.message || String(error)}
        </p>
        <p className="text-[11px] leading-5 text-zinc-500 dark:text-zinc-500">
          其余面板不受影响；若持续失败，多半是该接口响应结构异常（HTTP 200 但响应体不是预期格式）。
        </p>
        <button
          onClick={this.retry}
          className="rounded-md border border-zinc-300 px-2.5 py-1 text-[11px] text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
        >
          重试
        </button>
      </div>
    );
  }
}
