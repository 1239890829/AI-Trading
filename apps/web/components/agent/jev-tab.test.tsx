import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";

const overviewMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getJevOverview: () => overviewMock(),
}));

const { JevTab } = await import("./jev-tab");

afterEach(() => {
  cleanup();
  overviewMock.mockReset();
});

describe("JevTab", () => {
  it("显示真实结构化 JEV trace 与降级说明，不伪造思维链", async () => {
    overviewMock.mockResolvedValue({
      status: {
        state: "ready",
        model: "jev-1.13.0",
        modes: { alert_triage: "shadow", event_aux: "off", assistant_tools: "shadow", assistant_verify: "off" },
        usage_log_enabled: true,
        metrics: { calls: 1, ok: 1, failed: 0, skipped: 0, input_tokens: 10, output_tokens: 2,
                   avg_latency_ms: 12, purposes: {}, comparisons: {}, routing: {} },
      },
      historical_usage: {
        calls: 12, ok: 10, failed: 1, skipped: 1, input_tokens: 100, output_tokens: 20,
        avg_latency_ms: 80, malformed_lines: 0, purposes: { alert_triage: 12 }, models: { "jev-1.13.0": 12 },
      },
      recent_decisions: [{
        at_utc: "2026-09-23T01:00:00+00:00",
        purpose: "alert_triage",
        status: "ok",
        model: "jev-1.13.0",
        answers: { verdict: { type: "choice", choice: "notify", confidence: 0.91,
                              probabilities: { notify: 0.91, ignore: 0.06, escalate: 0.03 } } },
        latency_ms: 42,
        reason: null,
      }],
      trace_scope: "runtime_only",
      privacy: "不保存 state、用户正文、prompt、criteria 或 evidence 正文",
      fallbacks: { alert_triage: "JEV不可用/低置信→DeepSeek；再失败按既有规则提醒" },
    });

    render(<JevTab />);
    await waitFor(() => expect(screen.getByText("jev-1.13.0")).toBeTruthy());
    expect(screen.getByText(/notify · conf 91.0%/)).toBeTruthy();
    expect(screen.getByText(/ignore 6.0%/)).toBeTruthy();
    expect(screen.getByText(/JEV不可用\/低置信/)).toBeTruthy();
    expect(screen.getByText(/不展示输入正文，也不伪造“思维链”/)).toBeTruthy();
  });

  it("没有调用时明确显示 JEV 未参与，不制造占位判断", async () => {
    overviewMock.mockResolvedValue({
      status: { state: "disabled", model: "jev-1.13.0", modes: {}, usage_log_enabled: false,
                metrics: { calls: 0, ok: 0, failed: 0, skipped: 0, input_tokens: 0, output_tokens: 0,
                           avg_latency_ms: 0, purposes: {}, comparisons: {}, routing: {} } },
      historical_usage: { calls: 0, ok: 0, failed: 0, skipped: 0, input_tokens: 0, output_tokens: 0,
                          avg_latency_ms: 0, malformed_lines: 0, purposes: {}, models: {} },
      recent_decisions: [],
      trace_scope: "runtime_only",
      privacy: "不保存正文",
      fallbacks: {},
    });

    render(<JevTab />);
    await waitFor(() => expect(screen.getByText("已关闭")).toBeTruthy());
    expect(screen.getByText(/JEV 未参与时这里保持为空/)).toBeTruthy();
  });
});
