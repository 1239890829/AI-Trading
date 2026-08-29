"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { StockDetailPanel } from "@/components/stock-detail";

export default function StockPage() {
  const params = useParams<{ symbol: string }>();
  const symbol = String(params.symbol ?? "");

  if (!/^\d{6}$/.test(symbol)) {
    return (
      <main className="h-full overflow-y-auto px-4 py-6">
        <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-3 text-sm text-amber-600 dark:text-amber-300">
          非法股票代码：{symbol}
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto flex h-full w-full max-w-[1600px] flex-col gap-3 overflow-y-auto px-4 py-3">
      <nav className="shrink-0 text-xs text-zinc-400">
        <Link href="/workbench" className="hover:underline">
          工作台
        </Link>
        <span className="mx-1">/</span>
        <span className="font-mono">{symbol}</span>
      </nav>
      <StockDetailPanel symbol={symbol} />
      <p className="shrink-0 text-xs text-zinc-500">
        筹码 / 研报 / AI 分析等随 Phase 5-7 推入；完整财务表与资金明细见工作台右栏对应页签。
      </p>
    </main>
  );
}
