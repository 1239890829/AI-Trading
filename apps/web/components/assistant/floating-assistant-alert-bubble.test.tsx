import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";

// IMP-086 replaces proactive assistant reminders with manual conversation.
import { FloatingAssistant } from "@/components/assistant/floating-assistant";
import { loadSessions } from "@/lib/assistant-sessions";

beforeEach(() => {
  localStorage.clear();
  vi.useFakeTimers();
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.unstubAllGlobals(); });

it("mount, time, visibility and opening chat never request proactive reminders or show their entry points", async () => {
  const fetchMock = vi.fn(async (_url: string | URL) => new Response(JSON.stringify({data: {stocks: [], themes: []}})));
  vi.stubGlobal("fetch", fetchMock);
  render(<FloatingAssistant />);
  await act(async () => { await vi.advanceTimersByTimeAsync(150_000); document.dispatchEvent(new Event("visibilitychange")); });
  fireEvent.keyDown(screen.getByTestId("assistant-ball"), {key: "Enter"});
  fireEvent.click(screen.getByRole("button", {name: "最小化"}));
  expect(fetchMock.mock.calls.every(([url]) => !String(url).includes("triage"))).toBe(true);
  for (const testId of ["assistant-alert-bubble", "assistant-alert-summary", "assistant-alert-dot", "assistant-alert-inbox", "assistant-alert-all"])
    expect(screen.queryByTestId(testId)).toBeNull();
  expect(screen.queryByRole("button", {name: /提醒/})).toBeNull();
});

it("manual chat still consumes SSE, renders tool receipts and stores the conversation", async () => {
  let emit!: (event: Record<string, unknown>) => void;
  let close!: () => void;
  const stream = new ReadableStream<Uint8Array>({start(controller) {
    emit = event => controller.enqueue(new TextEncoder().encode(`data: ${JSON.stringify(event)}\n\n`));
    close = () => controller.close();
  }});
  const fetchMock = vi.fn(async (url: string | URL) => String(url).includes("/assistant/chat")
    ? new Response(stream, {headers: {"Content-Type": "text/event-stream"}})
    : new Response(JSON.stringify({data: {stocks: [], themes: []}})));
  vi.stubGlobal("fetch", fetchMock);
  render(<FloatingAssistant />);
  fireEvent.keyDown(screen.getByTestId("assistant-ball"), {key: "Enter"});
  fireEvent.change(screen.getByTestId("assistant-input"), {target: {value: "核对行情来源"}});
  await act(async () => { fireEvent.click(screen.getByRole("button", {name: "发送"})); });
  await act(async () => {
    emit({type: "tools", used: ["market_overview"], labels: ["市场概览"]});
    emit({type: "delta", text: "行情依据及未知项"});
    emit({type: "done"});
    close();
  });
  expect(screen.getByTestId("assistant-panel").textContent).toContain("行情依据及未知项");
  expect(screen.getByTestId("assistant-panel").textContent).toContain("市场概览");
  expect(fetchMock.mock.calls.filter(([url]) => String(url).includes("/assistant/chat"))).toHaveLength(1);
  expect(loadSessions()[0].messages.map(message => message.role)).toEqual(["user", "assistant"]);
  expect(fetchMock.mock.calls.every(([url]) => !String(url).includes("triage"))).toBe(true);
});
