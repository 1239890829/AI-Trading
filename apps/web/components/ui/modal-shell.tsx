"use client";

/**
 * 通用弹窗外壳（2026-09-15 统一）。
 *
 * ## 为什么要这一步
 * 到本轮为止，全站有 **5 个各自实现 portal 的弹窗**：
 * `news-modal` / `concept-detail-modal` / `picks/pick-detail-modal` /
 * `detail/detail-modal` / `detail/symbol-detail-modal`。
 * `detail-modal.tsx` 在 2026-09-09 诞生时就已经把这件事写进头注——
 * 「此前资讯弹窗、概念弹窗、精选详情各写各的」——但它自己也没抽公共外壳，
 * 于是"各写各的"从 3 处变成 5 处。后果不是丑，是**行为不一致**：
 * - 遮罩点击：3 个用 `onMouseDown + target 判定`、1 个用 `onClick + 子元素 stopPropagation`
 *   （后者要求面板显式阻止冒泡，漏一处就"点正文即关闭"）；
 * - 遮罩浓度：`/50` 与 `/60` 混用；毛玻璃 3 个有一个没加；
 * - `role="dialog"` 有的挂在**遮罩**上、有的挂在**面板**上 ⇒ 无障碍树里
 *   屏幕阅读器读到的"对话框"边界不一致；
 * - Esc 监听、z-index、圆角、头尾边框各写一遍 ⇒ 每加一个弹窗就重新赌一次。
 *
 * ## 统一后的约定（新弹窗一律走这里）
 * - 遮罩 `role="presentation"` + `onMouseDown`（**面板无需 stopPropagation**）；
 * - 面板 `role="dialog"` + `aria-modal` + `aria-label`（label 必填）+ 可选 `testid`；
 * - Esc 关闭、`overscroll-contain`（防滚动链穿透）；
 * - 三个尺寸档 `sm|md|lg`、`zIndex` 可调（内容详情弹窗需要压在其他弹窗之上）；
 * - 头部（标题区 + 统一关闭按钮）、正文（滚动槽）、底部（口径行）三段式，
 *   样式只在这里定义一次。
 *
 * ## 刻意的边界
 * **不锁 body 滚动**：`app/layout.tsx` 的 body 恒为 `h-screen overflow-hidden`，
 * 各页面滚动容器都不是 portal 的 DOM 祖先 ⇒ 滚动链不会穿透到背景页，
 * 加锁只会是 no-op 死代码（同 `symbol-detail-modal` 的判断）。
 */
import { useCallback, useEffect, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";

/** 弹窗尺寸档：sm 窄卡 / md 常规正文 / lg 全功能面板。 */
export type ModalSize = "sm" | "md" | "lg";

/**
 * 尺寸档 → 容器类。
 * - `sm` / `md`：高度**随内容**（`max-h-85vh`），适合正文与卡片；
 * - `lg`：高度**确定**（`min(1000px,92vh)`）——详情面板内部靠 `flex-1` 撑满图表，
 *   必须拿到确定高度（全高契约，见 `components/panel.tsx` 头注）。
 */
const SIZE_CLASS: Record<ModalSize, string> = {
  sm: "max-h-[85vh] w-full max-w-md",
  md: "max-h-[85vh] w-full max-w-2xl",
  lg: "h-[min(1000px,92vh)] w-[min(1600px,96vw)]",
};

/** 客户端挂载标志的空订阅（portal 目标 `document.body` 在 SSR 不存在）。 */
const subscribeNoop = () => () => {};

export interface ModalShellProps {
  onClose: () => void;
  /** 无障碍名称。**必填**——它是屏幕阅读器与自动化测试识别"这是哪个弹窗"的依据。 */
  label: string;
  /** 面板的 `data-testid`（挂在 `role="dialog"` 的元素上）。 */
  testid?: string;
  size?: ModalSize;
  /** 层级：默认 50。内容详情弹窗（可从其他弹窗内打开）用 60。 */
  zIndex?: number;
  /** 头部内容（标题/元信息）。省略则不渲染头部，也不渲染关闭按钮。 */
  header?: React.ReactNode;
  /** 底部口径行。省略则不渲染。 */
  footer?: React.ReactNode;
  /** 正文额外类。默认「整体滚动 + 常规内边距」；内部自管滚动的弹窗传 `flex flex-col overflow-hidden`。 */
  bodyClassName?: string;
  /** 正文容器的 `data-testid`。 */
  bodyTestId?: string;
  /** 面板圆角：默认 `xl`（`2xl` 用于概念弹窗这类大圆角场景）。 */
  radius?: "xl" | "2xl";
  children: React.ReactNode;
}

export function ModalShell({
  onClose,
  label,
  testid,
  size = "md",
  zIndex = 50,
  header,
  footer,
  bodyClassName = "overflow-y-auto px-5 py-4",
  bodyTestId,
  radius = "xl",
  children,
}: ModalShellProps) {
  // 客户端挂载标志：服务端快照 false、客户端 true（与 detail-modal 同款惯用法）。
  const mounted = useSyncExternalStore(subscribeNoop, () => true, () => false);

  // Esc 关闭（全站一致）
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // 只有点在遮罩**本身**才关闭；点面板内部（含拖拽选中）不关。
  // 用 `onMouseDown` 而非 `onClick`：正文里拖选文本后在弹窗外松开不会误关。
  const handleBackdrop = useCallback(
    (e: React.MouseEvent) => {
      if (e.target === e.currentTarget) onClose();
    },
    [onClose],
  );

  if (!mounted) return null;

  return createPortal(
    <div
      className="anim-backdrop-in fixed inset-0 flex items-center justify-center overscroll-contain bg-black/50 p-2 backdrop-blur-sm sm:p-4"
      style={{ zIndex }}
      onMouseDown={handleBackdrop}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={label}
        data-testid={testid}
        className={`anim-scale-in flex flex-col overflow-hidden border border-zinc-200 bg-white shadow-2xl dark:border-zinc-800 dark:bg-zinc-900 ${
          radius === "2xl" ? "rounded-2xl" : "rounded-xl"
        } ${SIZE_CLASS[size]}`}
      >
        {header !== undefined && (
          <div className="flex shrink-0 items-start justify-between gap-3 border-b border-zinc-100 px-5 py-3.5 dark:border-zinc-800/80">
            <div className="min-w-0 flex-1">{header}</div>
            <CloseButton onClose={onClose} />
          </div>
        )}
        <div
          data-testid={bodyTestId}
          className={`flex min-h-0 flex-1 flex-col ${bodyClassName}`}
        >
          {children}
        </div>
        {footer !== undefined && (
          <div className="flex shrink-0 items-center justify-between gap-3 border-t border-zinc-100 px-5 py-2.5 text-[11px] leading-relaxed text-zinc-600 dark:border-zinc-800/80 dark:text-zinc-400">
            {footer}
          </div>
        )}
      </div>
    </div>,
    document.body,
  );
}

/** 全站统一关闭按钮（× 图标 + `aria-label`）。 */
function CloseButton({ onClose }: { onClose: () => void }) {
  return (
    <button
      type="button"
      onClick={onClose}
      aria-label="关闭"
      title="关闭（Esc）"
      className="shrink-0 rounded p-1 text-zinc-600 transition-colors hover:bg-zinc-100 hover:text-zinc-700 dark:text-zinc-400 dark:hover:bg-zinc-800 dark:hover:text-zinc-200"
    >
      <svg width="14" height="14" viewBox="0 0 14 14" fill="none" aria-hidden>
        <path d="M2 2l10 10M12 2L2 12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      </svg>
    </button>
  );
}
