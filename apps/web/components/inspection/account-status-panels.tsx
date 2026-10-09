"use client";

import { AccountScopePanel } from "@/components/detail/account-scope-panel";
import { RealPositionPanel } from "@/components/detail/real-position-panel";
import { ProductionOperations } from "@/components/agent/production-operations";
import { FilterMenu } from "@/components/ui/filter-menu";
import { useState } from "react";
import type { InspectionRequest } from "./inspection-context";

export function AccountInspectionPanel({ request }: { request: Extract<InspectionRequest, {kind: "account"}> }) {
  const [scope, setScope] = useState(request.scope);
  return <div className="min-h-0 space-y-3">
    <FilterMenu label="账户" value={scope} options={[
      {key: "manual", label: "手工记录", title: "用户记账，非券商验证"}, {key: "main", label: "手工模拟", title: "main 独立账户，只读核对"},
      {key: "daily", label: "每日精选影子", title: "读取最新已保存精选的执行回执"}, {key: "hunting", label: "机会影子", title: "已有合格买点的独立模拟证据"},
    ]} onChange={setScope} />
    <p className="text-xs text-zinc-600 dark:text-zinc-400">切换仅查看该账户；不启用影子、不合并收益、不初始化资金。</p>
    <AccountScopePanel readOnly account={scope === "main" ? "paper" : scope} />
    {scope === "manual" && <RealPositionPanel readOnly />}
  </div>;
}

export function SystemStatusPanel() {
  // Reuses the original bounded command + confirmation + receipt owner.
  // Mounting performs only the status GET, never invokes a command.
  return <ProductionOperations />;
}
