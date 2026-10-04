"use client";

import { MarketLensPicker } from "@/components/ui/workspace-deck";
import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import { ThemesTab } from "@/components/tape/themes-tab";
import { LimitUpTab } from "@/components/tape/limit-up-tab";
import { LimitDownTab } from "@/components/tape/limit-down-tab";
import { LonghuTab } from "@/components/tape/longhu-tab";
import { FadeSwap, PageSkeletonFallback } from "@/components/ui/loading";

/**
 * 盘面页（2026-09-01 系统重构，docs/archive/architecture-redesign.md §一.1.2）：
 * 涨停池 / 题材 / 龙虎榜 合并为一个入口的三个 tab——
 * 同属"盘面生态"参考，分开看要来回切，且除龙虎榜外都消费同一份涨停数据。
 *
 * 2026-09-01 评审 D1：「板块排行」tab 移除——板块数据与工作台详情右列
 * 「板块」页签同源重复（同一条 /api/boards 链路），板块是行业横截面，
 * 与盘面页的短线生态定位不契合；能力保留在工作台详情，零损失。
 *
 * tab 以 ?tab= 查询参数为真相源（可分享、可回退）；其余查询参数由各 tab 自治。
 */

const TABS = [
  { key: "themes", label: "题材梯队" },
  { key: "limitup", label: "涨停生态" },
  { key: "limitdown", label: "跌停" },
  { key: "longhu", label: "龙虎榜" },
] as const;

type TabKey = (typeof TABS)[number]["key"];

function TapeInner() {
  const sp = useSearchParams();
  const raw = sp.get("tab");
  const tab: TabKey = TABS.some((t) => t.key === raw) ? (raw as TabKey) : "themes";


  return (
    <main className="task-page mx-auto flex h-full w-full max-w-[1600px] flex-col px-4 py-3">
      <div className="workspace-masthead">
        <div><h1>市场全景</h1><p className="workspace-kicker">梯队结构、涨停证据与资金关注</p></div>
        <MarketLensPicker selected={tab} search={sp.toString()} />
      </div>

      {/* tab 切换统一 fade 过渡（2026-09-04）：h-full 保持子 tab 内部 flex 布局 */}
      <FadeSwap swapKey={tab} className="task-scroll min-h-0 flex-1">
        {tab === "themes" && <ThemesTab />}
        {tab === "limitup" && <LimitUpTab />}
        {tab === "limitdown" && <LimitDownTab />}
        {tab === "longhu" && <LonghuTab />}
      </FadeSwap>
    </main>
  );
}

export default function TapePage() {
  return (
    <Suspense fallback={<PageSkeletonFallback label="盘面页加载中" />}>
      <TapeInner />
    </Suspense>
  );
}
