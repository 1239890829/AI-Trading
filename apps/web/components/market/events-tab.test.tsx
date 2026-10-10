import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import type { ImpactEvent } from "@/lib/api";
import { EventsTab } from "./events-tab";

afterEach(cleanup);
const resource = vi.hoisted(() => ({ data: null as unknown }));
vi.mock("@/hooks/use-polling-fetch", () => ({ useResource: () => ({ data: resource.data, error: null, refresh: vi.fn() }) }));
vi.mock("@/components/inspection/surface-scope", () => ({ useSurfaceScope: () => ({ searchParams: new URLSearchParams() }) }));

const base: ImpactEvent = {
  id: 1, title: "规则尚无明确方向", url: null, source: "财联社", source_tier: 4,
  published_at: "2026-10-10 09:00:00", fact_kind: "fact", certainty: "done",
  category: "corporate", half_life_hours: 48, source_symbol: null, is_active: true,
  directions: [], four_category: "hot", four_label: "市场热点", impact_level: "L2", tags: [],
};

describe("EventsTab judgement display", () => {
  it("keeps status visible with no direction and marks unvalidated model rows", () => {
    resource.data = { countsAll: {}, tagCounts: {}, items: [
      { ...base, judge_status: "neutral", judge_reason: "证据不足已超时" },
      { ...base, id: 2, title: "模型补充关联", judge_status: "pending", directions: [
        { target_type: "theme", target: "存储芯片", direction: 1, strength: 1, chain: "", basis: "辅助解释", matched_by: "llm_aux" },
      ] },
    ] };
    render(<EventsTab />);
    expect(screen.getByText("未明超时").getAttribute("title")).toContain("证据不足已超时");
    expect(screen.getByText("利好假设")).toBeTruthy();
    expect(screen.queryByText("利好")).toBeNull();
    expect(screen.queryByText("待判")).toBeNull();
    expect(screen.getByText(/不代表正在后台排队判读/)).toBeTruthy();
  });
});
