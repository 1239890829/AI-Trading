"use client";

import { RealPositionPanel } from "@/components/detail/real-position-panel";
import { ProductionOperations } from "@/components/agent/production-operations";
import type { InspectionRequest } from "./inspection-context";

export function AccountInspectionPanel({ request }: { request: Extract<InspectionRequest, {kind: "account"}> }) {
  return <div className="min-h-0 space-y-3">
    {request.scope !== "manual" && <p className="text-xs">模拟验证已转为内部运行。请在选股的“验证统计”核对入选观察与实际成交。</p>}
    <RealPositionPanel readOnly />
  </div>;
}

export function SystemStatusPanel() {
  // Reuses the original bounded command + confirmation + receipt owner.
  // Mounting performs only the status GET, never invokes a command.
  return <ProductionOperations />;
}
