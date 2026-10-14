import {describe, expect, it} from "vitest";
import {limitQualification} from "./limit-qualification";
import type {LimitPoolSnapshot} from "./api";
import type {LimitUpRecord} from "@/types/market";

const row: LimitUpRecord = {symbol: "600825", trade_date: "2026-10-14", source: "ths", quality: "high", quality_reasons: [], received_at: "2026-10-14T09:40:00+08:00", reason: "拟收购"};
const empty = (): LimitPoolSnapshot<LimitUpRecord> => ({trade_date: "2026-10-14", pool: [], break_pool: [], coverage: {limit_up: {state: "success", error: null}, limit_break: {state: "success", error: null}}, qualification_observations: {}});
describe("same-day source-backed reason eligibility", () => {
  it("ordinary stocks and unknown inputs do not acquire a reason tab", () => {
    for (const value of [undefined, empty(), {...empty(), trade_date: "2026-10-13"}]) expect(limitQualification(value, "600825", "2026-10-14").eligible).toBe(false);
    expect(limitQualification({...empty(), pool: [row]}, "600825", null).eligible).toBe(false);
  });
  it.each(["pool", "break_pool"] as const)("an actual %s member qualifies", field => {
    const value = limitQualification({...empty(), [field]: [row]}, "600825", "2026-10-14");
    expect(value.eligible).toBe(true);
    expect(value.kind).toBe(field === "pool" ? "limit_up" : "limit_break");
  });
  it("one failed domain does not erase the independently successful other domain", () => {
    const data = empty(); data.coverage!.limit_up = {state: "error", error: "unavailable"}; data.break_pool = [row];
    expect(limitQualification(data, "600825", "2026-10-14").eligible).toBe(true);
    data.coverage!.limit_break = {state: "error", error: "unavailable"};
    expect(limitQualification(data, "600825", "2026-10-14").eligible).toBe(false);
  });
  it("affirmative same-day observations survive failures, without inventing a current pool row", () => {
    const data = empty(); data.coverage!.limit_up = {state: "error", error: "failed"};
    data.qualification_observations = {"600825": [{symbol: "600825", trade_date: "2026-10-14", kind: "limit_break", source: "ths", observed_at: "2026-10-14T09:35:00+08:00"}]};
    const value = limitQualification(data, "600825", "2026-10-14");
    expect(value.eligible).toBe(true); expect(value.row).toBeUndefined();
    expect(limitQualification(data, "600825", "2026-10-15").eligible).toBe(false);
  });
  it.each([{symbol: "600127"}, {trade_date: "2026-10-13"}, {source: ""}, {quality: "invalid" as const}, {quality: "stale" as const}, {quality: "low" as const}])("rejects mismatched or unconfirmed rows %j", patch => {
    expect(limitQualification({...empty(), pool: [{...row, ...patch}]}, "600825", "2026-10-14").eligible).toBe(false);
  });
  it("a saved reason is retained as text but cannot establish first-time stale membership", () => {
    const data = {...empty(), pool: [{...row, quality: "stale" as const}], reason_observations: {"600825": [{symbol: "600825", trade_date: "2026-10-14", source: "ths", reason: "拟收购", content_hash: "fixture", observed_at: "2026-10-14T09:35:00+08:00", received_at: null}]}};
    const value = limitQualification(data, "600825", "2026-10-14");
    expect(value.eligible).toBe(false);
    expect(value.previous?.reason).toBe("拟收购");
  });
});
