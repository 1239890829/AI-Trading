"use client";

/**
 * 路由级错误边界（技术评审 F1）：任何页面渲染/数据异常不再白屏，
 * 落到这张错误卡——保留重试入口，错误信息原文展示（便于定位）。
 */

export default function PageError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  return (
    <main className="flex h-full items-center justify-center px-4">
      <div className="w-full max-w-md rounded-xl border border-amber-500/40 bg-amber-500/5 p-6 text-center">
        <h2 className="text-base font-semibold text-amber-600 dark:text-amber-300">页面加载出错</h2>
        <p className="mt-2 break-all text-xs leading-5 text-zinc-500 dark:text-zinc-400">
          {error.message}
          {error.digest && <span className="ml-1 font-mono text-zinc-400">[{error.digest}]</span>}
        </p>
        <button
          onClick={reset}
          className="mt-4 rounded-md border border-zinc-300 px-3 py-1.5 text-sm text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
        >
          重试
        </button>
        <p className="mt-3 text-[11px] text-zinc-400">
          反复失败请确认后端已启动：<span className="font-mono">cd backend &amp;&amp; uvicorn app.main:app --port 8000</span>
        </p>
      </div>
    </main>
  );
}
