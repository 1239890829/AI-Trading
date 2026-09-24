import { describe, expect, it } from "vitest";
import { eventDetailPayload, eventNewsItem } from "./event-view";
import type { ImpactEvent } from "./api";

const event: ImpactEvent = {
  id: 8, title: "来源事件", url: "https://example.com/8", source: "财联社",
  source_tier: 4, published_at: "2026-09-24 10:00:00", fact_kind: "fact",
  certainty: "done", category: "corporate", half_life_hours: 48,
  source_symbol: null, is_active: true, directions: [], four_category: "hot",
  four_label: "市场热点", impact_level: "L2", tags: [],
  interpretation_ref: { event_id: 8, version_id: 19, observation_id: 7,
    available_at: "2026-09-24 10:02:00", state: "active" },
};

describe("event version at display entry", () => {
  it("keeps the same version in source and summary modals", () => {
    expect(eventNewsItem(event).evidence).toContain("#19 · 生效 · 2026-09-24 10:02:00");
    expect(eventDetailPayload({ ...event, url: null }).meta).toContainEqual({
      label: "列表解释版本", value: "#19 · 生效 · 2026-09-24 10:02:00",
    });
  });

  it("does not invent a version for legacy events", () => {
    expect(eventNewsItem({ ...event, interpretation_ref: null }).evidence).toContain("历史解释版本未知");
  });
});
