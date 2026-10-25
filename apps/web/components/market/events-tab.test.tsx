import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ImpactEvent } from "@/lib/api";
import { ImpactEventsView as EventsTab } from "./events-tab";

afterEach(() => {cleanup(); detail.open.mockClear();});
const detail = vi.hoisted(() => ({open: vi.fn()}));
vi.mock("@/components/detail/detail-modal", () => ({useDetailModal: () => detail}));
vi.mock("@/components/event-panel", () => ({StockPools: () => <div>独立标的池内容</div>}));
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
  it("filters recorded directions without promoting model guesses or deleting the original list", () => {
    const direction = {target_type: "theme" as const, target: "存储芯片", direction: 1, strength: 1, chain: "", basis: "原文线索"};
    resource.data = {countsAll: {}, tagCounts: {}, items: [
      {...base, title: "规则线索", directions: [{...direction, matched_by: "chain"}]},
      {...base, id: 2, title: "无明确方向"},
      {...base, id: 3, title: "模型假设", directions: [{...direction, matched_by: "llm_aux"}]},
      {...base, id: 4, title: "已撤回线索", directions: [direction], interpretation_ref: {version_id: 4, state: "withdrawn"}},
    ]};
    render(<EventsTab />);
    fireEvent.click(screen.getByRole("button", {name: "仅看方向记录"}));
    expect(screen.getByText("规则线索")).toBeTruthy();
    for (const title of ["无明确方向", "模型假设", "已撤回线索"]) expect(screen.queryByText(title)).toBeNull();
    expect(screen.getByText(/显示 1 \/ 4 条/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "仅看方向记录"}));
    expect(screen.getByText("无明确方向")).toBeTruthy();
    expect(screen.getByText("模型假设")).toBeTruthy();
  });
  it("keeps pool expansion, pool content and related links separate from the title action", () => {
    resource.data = {countsAll: {}, tagCounts: {}, items: [{...base, rank_score: 55, directions: [
      {target_type: "theme", target: "存储芯片", direction: 1, strength: 1, chain: "供需变化", basis: "公开公告"},
    ]}]};
    const ancestor = vi.fn();
    render(<div onClick={ancestor}><EventsTab /></div>);
    fireEvent.click(screen.getByText("55分"));
    expect(detail.open).toHaveBeenCalledTimes(1);
    const poolButton = screen.getByRole("button", {name: "标的池 ↗"});
    fireEvent.click(poolButton);
    expect(poolButton.getAttribute("aria-expanded")).toBe("true");
    expect(poolButton.getAttribute("aria-controls")).toBe("event-pool-1");
    fireEvent.click(screen.getByText("独立标的池内容"));
    fireEvent.click(screen.getByText("存储芯片"));
    expect(detail.open).toHaveBeenCalledTimes(1);
    expect(ancestor).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name: "收起标的池"}));
    expect(poolButton.getAttribute("aria-expanded")).toBe("false");
  });

  it("keeps status visible with no direction and marks unvalidated model rows", () => {
    resource.data = { countsAll: {}, tagCounts: {}, items: [
      { ...base, judge_status: "neutral", judge_reason: "证据不足已超时" },
      { ...base, id: 2, title: "模型补充关联", judge_status: "pending", directions: [
        { target_type: "theme", target: "存储芯片", direction: 1, strength: 1, chain: "", basis: "辅助解释", matched_by: "llm_aux" },
      ] },
    ] };
    render(<EventsTab />);
    expect(screen.getByText("未明超时").getAttribute("title")).toContain("证据不足已超时");
    expect(screen.getByText("模型正向假设")).toBeTruthy();
    expect(screen.queryByText("利好")).toBeNull();
    expect(screen.queryByText("待判")).toBeNull();
    expect(screen.getByText(/不代表正在后台排队判读/)).toBeTruthy();
  });
});
