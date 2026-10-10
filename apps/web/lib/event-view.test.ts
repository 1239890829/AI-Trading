import { describe, expect, it } from "vitest";
import { eventDetailPayload, eventDirectionView, eventJudgeView, eventNewsItem } from "./event-view";
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

describe("event direction and judgement boundaries", () => {
  const direction = { target_type: "theme", target: "存储芯片", direction: 0, strength: 1, chain: "", basis: "仅匹配关联" };

  it("does not treat a zero target as judged because another target has a direction", () => {
    const e = { ...event, judge_status: "judged" as const, directions: [direction, { ...direction, target: "AI", direction: 1 }] };
    expect(eventJudgeView(e).text).toBe("已判定");
    expect(eventDirectionView(direction, e).text).toBe("方向未明");
  });

  it.each([
    ["pending", "方向未明"], ["neutral", "未明超时"], ["expired", "已过期"], ["unknown", "状态未知"],
  ] as const)("shows %s as %s even without direction rows", (judge_status, label) => {
    const e = { ...event, judge_status, judge_reason: "当前来源证据不足" };
    expect(eventJudgeView(e).text).toBe(label);
    expect(eventNewsItem(e).judgement).toContain("当前来源证据不足");
    expect(eventDetailPayload(e).meta).toContainEqual({ label: "判定状态", value: label });
    expect(eventDetailPayload(e).body).toContain("当前来源证据不足");
  });

  it("does not promise an AI queue or turn a timeout into established neutrality", () => {
    expect(eventJudgeView({ judge_status: "pending" }).title).toContain("不代表正在后台排队");
    expect(eventJudgeView({ judge_status: "neutral" }).title).toContain("不代表已经证实为中性");
    expect(eventDirectionView(direction, { judge_status: "neutral" }).text).toBe("方向未明");
  });

  it.each(["expired", "unknown"] as const)("keeps the original nonzero direction visible beside %s", judge_status => {
    expect(eventDirectionView({ ...direction, direction: 1 }, { judge_status }).text).toBe("利好");
    expect(eventDirectionView({ ...direction, direction: -1 }, { judge_status }).text).toBe("利空");
  });

  it.each([[1, "利好假设"], [-1, "利空假设"]] as const)("marks model direction %s as an unvalidated hypothesis", (value, text) => {
    const d = { ...direction, direction: value, matched_by: "llm_aux" };
    const e = { ...event, judge_status: "judged" as const, directions: [d] };
    expect(eventDirectionView(d, e).text).toBe(text);
    expect(eventDirectionView(d, e).title).toContain("尚未经人工验证");
    expect(eventDetailPayload(e).body).toContain("不能作为已判定事实");
    expect(eventNewsItem(e).judgement).toContain("不能作为已判定事实");
  });

  it("keeps a historical model attempt separate from the current interpretation", () => {
    const e = { ...event, judge_status: "unknown" as const, judge_status_label: "来源修订待复核", llm_judged_at: "2026-09-24 10:03:00" };
    expect(eventJudgeView(e).text).toBe("待复核");
    expect(eventJudgeView(e).title).toContain("AI辅助曾于 2026-09-24 10:03:00 尝试");
    expect(eventJudgeView(e).title).toContain("不能据此确认当前方向");
  });

  it("keeps a symbol association in the symbol context rather than opening a numeric theme", () => {
    const e = { ...event, directions: [{ ...direction, target_type: "symbol", target: "600519" }] };
    expect(eventDetailPayload(e).symbol).toBe("600519");
    expect(eventDetailPayload(e).theme).toBeNull();
  });

  it("shows unapplied model notes without replacing zero rows or other ruled directions", () => {
    const e = { ...event, judge_status: "judged" as const,
      directions: [direction, { ...direction, target: "AI", direction: -1 }],
      llm_aux_note: "LLM 辅助假设未应用：存储芯片可能受益；依据尚待复核" };
    expect(eventDirectionView(direction, e).text).toBe("方向未明");
    expect(eventDirectionView(e.directions[1], e).text).toBe("利空");
    expect(eventJudgeView(e).text).toBe("已判定");
    expect(eventNewsItem(e).judgement).toContain(`AI有假设，未应用：${e.llm_aux_note}`);
    expect(eventDetailPayload(e).body).toContain(e.llm_aux_note);
  });

  it("does not infer applied hypotheses or human validation from unknown review fields", () => {
    const e = { ...event, judge_status: "pending" as const, directions: [direction],
      review_note: "AI认为利好", llm_aux_note: null,
      interpretation_ref: { ...event.interpretation_ref!, state: "active" as const } };
    expect(eventDirectionView(direction, e).text).toBe("方向未明");
    expect(eventJudgeView(e).text).toBe("方向未明");
    expect(eventNewsItem(e).judgement).not.toContain("AI有假设，未应用");
    expect(eventDetailPayload(e).body).not.toContain(e.review_note);
  });
});
