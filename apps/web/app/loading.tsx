/**
 * 路由级加载骨架（技术评审 F1）：Segment 切换瞬间的占位，防内容跳变。
 * 样式与各页头部结构对齐：标题行 + 卡片骨架，居中限宽。
 */

export default function Loading() {
  return (
    <main className="mx-auto h-full w-full max-w-[1600px] animate-pulse px-4 py-3">
      <div className="h-6 w-48 rounded bg-zinc-200 dark:bg-zinc-800" />
      <div className="mt-3 h-4 w-72 rounded bg-zinc-100 dark:bg-zinc-800/60" />
      <div className="mt-6 grid gap-3 lg:grid-cols-[340px,minmax(0,1fr)]">
        <div className="h-64 rounded-xl bg-zinc-100 dark:bg-zinc-800/60" />
        <div className="h-64 rounded-xl bg-zinc-100 dark:bg-zinc-800/60" />
      </div>
    </main>
  );
}
