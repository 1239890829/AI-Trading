import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * 生成期反馈（2026-09-11 用户反馈）：此前取数/思考期间界面完全静止，
 * 用户「一直在等待，还以为不动了」。这里钉住两条外部可见行为：
 *  - status 事件到达后必须出现「思考中…／正在取数：xxx」；
 *  - 正文回流后提示消失、改为行内光标（不占位、不重复）。
 * 以及工具回执要显示**中文标签**（后端 labels），不是英文键名。
 */

// lib/api 的其余导出保持真实；两个轮询接口在 jsdom 里没有后端，直接桩掉。
vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    getAgentBubbles: vi.fn(async () => []),
    ackAgentTriage: vi.fn(async () => undefined),
  };
});

import { FloatingAssistant } from "@/components/assistant/floating-assistant";

const enc = new TextEncoder();

/** 可手动推进的 SSE 流：能在"取数中"这一帧停下来断言，而不是一次跑完。 */
function makeStream() {
  let push!: (s: string) => void;
  let close!: () => void;
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      push = (s: string) => controller.enqueue(enc.encode(s));
      close = () => controller.close();
    },
  });
  const emit = (obj: Record<string, unknown>) =>
    push(`data: ${JSON.stringify(obj)}\n\n`);
  return { body, emit, close };
}

beforeEach(() => {
  localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  // vitest 未开 globals ⇒ testing-library 的自动清理不会注册，必须显式 cleanup，
  // 否则第二个用例会同时看到前一个渲染留下的悬浮球（getByTestId 报 multiple elements）
  cleanup();
});

function openPanel() {
  const ball = screen.getByTestId("assistant-ball");
  // 点按判定靠"未移动 <4px"；jsdom 下 PointerEvent 的 clientX 为 undefined，
  // dx 为 NaN ⇒ moved 保持 false ⇒ 视为点击（与真实拖拽路径不同，但等价于"点一下"）
  fireEvent.pointerDown(ball);
  fireEvent.pointerUp(ball);
}

async function send(text: string) {
  const input = screen.getByTestId("assistant-input");
  fireEvent.change(input, { target: { value: text } });
  await act(async () => {
    fireEvent.keyDown(input, { key: "Enter", shiftKey: false });
  });
}

describe("FloatingAssistant 生成期反馈", () => {
  it("status 事件驱动「思考中 / 正在取数」，正文回流后收起", async () => {
    const { body, emit, close } = makeStream();
    const fetchMock = vi.fn(async (url: string | URL) => {
      const u = String(url);
      if (u.includes("/assistant/chat")) {
        return new Response(body, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }) as unknown as Response;
      }
      return new Response(JSON.stringify({ data: { stocks: [], themes: [] } }), {
        status: 200,
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<FloatingAssistant />);
    await act(async () => {
      openPanel();
    });
    await send("华脉科技为什么大跌？");

    // ① 首帧只有 meta，还没有任何 status —— 不假设具体时序，直接推 status
    await act(async () => {
      emit({ type: "meta", provider: "claude_cli", model: "glm-5.3", sources: [] });
      emit({ type: "status", phase: "thinking" });
    });
    await waitFor(() =>
      expect(screen.getByTestId("assistant-activity").textContent).toContain("思考中"),
    );

    // ② 正在取数：必须带上中文标签（后端 tool_label 产出）
    await act(async () => {
      emit({ type: "status", phase: "tools", used: ["longhu"], label: "龙虎榜" });
    });
    await waitFor(() =>
      expect(screen.getByTestId("assistant-activity").textContent).toContain(
        "正在取数：龙虎榜",
      ),
    );

    // ③ 正文回流 → 思考提示消失（改为行内光标），并给出中文工具回执
    await act(async () => {
      emit({ type: "tools", used: ["longhu"], labels: ["龙虎榜"] });
      emit({ type: "delta", text: "华脉科技" });
      emit({ type: "delta", text: " 603042 低开后深 V。" });
      emit({ type: "done" });
      close();
    });

    expect(screen.queryByTestId("assistant-activity")).toBeNull();
    expect(screen.getByText(/603042/)).toBeTruthy();
    expect(screen.getByTestId("assistant-tools").textContent).toContain("龙虎榜");
    expect(screen.getByTestId("assistant-tools").textContent).not.toContain("longhu");
  });

  it("旧事件没有 labels 时退回键名，不显示空白回执", async () => {
    const { body, emit, close } = makeStream();
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL) =>
        String(url).includes("/assistant/chat")
          ? (new Response(body, { status: 200 }) as unknown as Response)
          : new Response(JSON.stringify({ data: { stocks: [], themes: [] } }), {
              status: 200,
            }),
      ),
    );

    render(<FloatingAssistant />);
    await act(async () => {
      openPanel();
    });
    await send("今天涨停池？");

    await act(async () => {
      emit({ type: "tools", used: ["limit_up"] }); // 无 labels（向后兼容）
      emit({ type: "delta", text: "今日涨停 38 家。" });
      emit({ type: "done" });
      close();
    });

    expect(screen.getByTestId("assistant-tools").textContent).toContain("limit_up");
  });
});
