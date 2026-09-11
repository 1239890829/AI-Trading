"use client";

/**
 * 路由级错误边界（技术评审 F1）：任何页面渲染/数据异常不再白屏，
 * 落到这张错误卡——保留重试入口，错误信息原文展示（便于定位）。
 *
 * **排查提示不写死单一原因**（2026-09-10 修正）：原文案只说「请确认后端已启动」，
 * 而实测有一类真实故障与后端是否启动无关——接口返回 HTTP 200，但响应体不是 API
 * Envelope（如被中间层换成 HTML），此时前端拿到 `undefined` 再取 `.length` 就崩，
 * 报错文本（如 `reviews.length`）指向的是页面代码，与根因隔了好几层。
 * 照「后端没启动」去排查会走偏，故改为「先确认进程、再看响应体」两步线索。
 */

export default function PageError({
  error,
  reset,
}: {
  error: Error & { digest?: string; code?: string };
  reset: () => void;
}) {
  return (
    <main className="flex h-full items-center justify-center px-4">
      <div className="w-full max-w-md rounded-xl border border-amber-500/40 bg-amber-500/5 p-6 text-center">
        <h2 className="text-base font-semibold text-amber-800 dark:text-amber-300">页面加载出错</h2>
        <p className="mt-2 break-all text-xs leading-5 text-zinc-600 dark:text-zinc-400">
          {error.message}
          {error.code && <span className="ml-1 font-mono text-zinc-600 dark:text-zinc-400">[{error.code}]</span>}
          {error.digest && <span className="ml-1 font-mono text-zinc-600 dark:text-zinc-400">[{error.digest}]</span>}
        </p>
        <button
          onClick={reset}
          className="mt-4 rounded-md border border-zinc-300 px-3 py-1.5 text-sm text-zinc-700 hover:bg-zinc-100 dark:border-zinc-700 dark:text-zinc-200 dark:hover:bg-zinc-800"
        >
          重试
        </button>
        <div className="mt-3 space-y-0.5 text-left text-[11px] leading-5 text-zinc-600 dark:text-zinc-400">
          <p>排查两步：</p>
          <p>
            ① 后端进程是否在跑 ——{" "}
            <span className="font-mono">cd backend &amp;&amp; uvicorn app.main:app --port 8000</span>
          </p>
          <p>
            ② 进程在跑仍失败 → 多为<b>接口响应异常</b>（HTTP 200 但响应体不是预期结构）：
            到浏览器 Network 面板看该请求的实际返回
          </p>
        </div>
      </div>
    </main>
  );
}
