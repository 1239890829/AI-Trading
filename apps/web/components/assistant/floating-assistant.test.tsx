import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

/**
 * 生成期反馈（2026-09-11 用户反馈）：此前取数/思考期间界面完全静止，
 * 用户「一直在等待，还以为不动了」。这里钉住两条外部可见行为：
 *  - status 事件到达后必须出现「思考中…／正在取数：xxx」；
 *  - 正文回流后提示消失、改为行内光标（不占位、不重复）。
 * 以及工具回执要显示**中文标签**（后端 labels），不是英文键名。
 */

import { FloatingAssistant } from "@/components/assistant/floating-assistant";
import { loadSessions, CURRENT_KEY, SESSIONS_KEY } from "@/lib/assistant-sessions";
import {__resetEntityDictCache, loadEntityDict} from "@/lib/entity-dict";

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
  __resetEntityDictCache();
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

describe("FloatingAssistant 视口缩放", () => {
  it("助手打开复用资讯已启动的实体词典，不另建网络读取", async () => {
    let finish!: (response: Response) => void;
    const fetcher = vi.fn(() => new Promise<Response>(resolve => {finish = resolve;}));
    vi.stubGlobal("fetch", fetcher);
    const articleOwner = loadEntityDict();
    render(<FloatingAssistant/>);
    await act(async () => openPanel());
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(async () => {
      finish(new Response(JSON.stringify({data: {stocks: [], themes: []}})));
      await articleOwner;
    });
    expect(screen.getByTestId("assistant-panel")).toBeTruthy();
  });

  it("真实 resize 保留左右贴边，未贴边的位置仅做边界约束", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ data: [] }))));
    const resize = (width: number) => {
      act(() => {
        vi.stubGlobal("innerWidth", width);
        window.dispatchEvent(new Event("resize"));
      });
    };

    for (const initial of [
      { x: 649, y: 240, side: "right", wideLeft: "1418px", narrowLeft: "368px" },
      { x: 16, y: 240, side: "left", wideLeft: "0px", narrowLeft: "0px" },
      { x: 240, y: 240, side: null, wideLeft: "240px", narrowLeft: "240px" },
    ]) {
      vi.stubGlobal("innerWidth", 713);
      const stored = JSON.stringify({ x: initial.x, y: initial.y });
      localStorage.setItem("ashare.assistant.pos", stored);
      const view = render(<FloatingAssistant />);
      const ball = await screen.findByTestId("assistant-ball");
      expect(ball.getAttribute("data-docked")).toBe(initial.side);

      resize(1440);
      expect(ball.getAttribute("data-docked")).toBe(initial.side);
      expect(ball.style.left).toBe(initial.wideLeft);
      resize(390);
      expect(ball.getAttribute("data-docked")).toBe(initial.side);
      expect(ball.style.left).toBe(initial.narrowLeft);
      // 缩放不改既有持久化契约；只有拖动结束才保存位置。
      expect(localStorage.getItem("ashare.assistant.pos")).toBe(stored);

      if (initial.side === null) {
        resize(200);
        expect(ball.style.left).toBe("178px");
      }
      view.unmount();
    }
  });
});

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

/**
 * 会话历史（IMP-004，2026-09-15）：此前会话只活在 `useState` 里，**刷新即失**。
 * 这里钉住用户能看见的四件事：刷新不丢 / 上一会话进列表且空会话不占位 /
 * 流式中切走时在途流的产出不得污染新会话 / 删除会话。
 */
describe("FloatingAssistant 会话历史", () => {
  /** 聊天走给定流，其余（entity-dict 等）返回空壳。 */
  function stubChat(body: ReadableStream<Uint8Array>) {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL) =>
        String(url).includes("/assistant/chat")
          ? (new Response(body, { status: 200 }) as unknown as Response)
          : new Response(JSON.stringify({ data: { stocks: [], themes: [] } }), { status: 200 }),
      ),
    );
  }

  /** 跑完一轮问答（发问 → 回答 → 收流），供后续用例复用。 */
  async function askAndFinish(stream: ReturnType<typeof makeStream>, q: string, answer: string) {
    await send(q);
    await act(async () => {
      stream.emit({ type: "delta", text: answer });
      stream.emit({ type: "done" });
      stream.close();
    });
  }

  it("会话落盘：刷新（卸载重挂）后对话仍在，标题取首句", async () => {
    const s1 = makeStream();
    stubChat(s1.body);
    const view = render(<FloatingAssistant />);
    await act(async () => {
      openPanel();
    });
    await askAndFinish(s1, "今天大盘怎么样？", "上证指数 3800 点。");

    await waitFor(() => expect(loadSessions(localStorage)).toHaveLength(1));
    expect(loadSessions(localStorage)[0].title).toBe("今天大盘怎么样？");

    // 模拟刷新：卸载再挂载 —— localStorage 留着，这正是"刷新不丢"的判据
    view.unmount();
    render(<FloatingAssistant />);
    await act(async () => {
      openPanel();
    });
    expect(screen.getByText(/3800 点/)).toBeTruthy();
  });

  it("新建会话：上一会话进历史列表、空会话不占额度，点回去可恢复", async () => {
    const s1 = makeStream();
    stubChat(s1.body);
    render(<FloatingAssistant />);
    await act(async () => {
      openPanel();
    });
    await askAndFinish(s1, "第一个会话的问题", "第一轮回答");
    await waitFor(() => expect(loadSessions(localStorage)).toHaveLength(1));

    fireEvent.click(screen.getByLabelText("新建会话"));
    await act(async () => {});
    expect(screen.queryByText(/第一轮回答/)).toBeNull();
    // 新会话是空的 ⇒ 不入列表（否则列表会被一串"新会话"占满）
    expect(loadSessions(localStorage)).toHaveLength(1);

    fireEvent.click(screen.getByTestId("assistant-history-toggle"));
    expect(screen.getByTestId("assistant-history-count").textContent).toContain("1 / 10");
    expect(screen.getByTestId("assistant-history-item").textContent).toContain("第一个会话的问题");

    fireEvent.click(screen.getByTestId("assistant-history-item"));
    await act(async () => {});
    expect(screen.getByText(/第一轮回答/)).toBeTruthy();
  });

  it("流式中切走会话：在途流的产出既不显示也不落盘", async () => {
    const s1 = makeStream();
    stubChat(s1.body);
    render(<FloatingAssistant />);
    await act(async () => {
      openPanel();
    });
    await askAndFinish(s1, "会话一的问题", "会话一的回答");
    await waitFor(() => expect(loadSessions(localStorage)).toHaveLength(1));
    const before = JSON.stringify(loadSessions(localStorage));

    // 会话二：发问后**故意不收流**，停在生成中
    const s2 = makeStream();
    stubChat(s2.body);
    fireEvent.click(screen.getByLabelText("新建会话"));
    await act(async () => {});
    await send("会话二的问题");

    // 切回会话一（列表里只有它一条）—— 在途的流应被弃掉
    fireEvent.click(screen.getByTestId("assistant-history-toggle"));
    fireEvent.click(screen.getByTestId("assistant-history-item"));
    await act(async () => {});
    expect(screen.getByText(/会话一的回答/)).toBeTruthy();

    // 迟到的 delta 到达：被弃的流不得写进"当前会话"，也不得落盘
    await act(async () => {
      s2.emit({ type: "delta", text: "会话二的半截回答" });
      s2.emit({ type: "done" });
      s2.close();
    });
    expect(screen.queryByText(/会话二的半截回答/)).toBeNull();
    expect(screen.getByText(/会话一的回答/)).toBeTruthy();
    expect(JSON.stringify(loadSessions(localStorage))).toBe(before);
  });

  it("流式中切走会话：在途请求失败也不得污染新会话", async () => {
    const s1 = makeStream();
    stubChat(s1.body);
    render(<FloatingAssistant />);
    await act(async () => {
      openPanel();
    });
    await askAndFinish(s1, "会话一的问题", "会话一的回答");
    await waitFor(() => expect(loadSessions(localStorage)).toHaveLength(1));
    const before = JSON.stringify(loadSessions(localStorage));

    // 会话二：请求一直挂起，等切走之后再让它失败（模拟断网 / 后端 500）
    let fail!: (e: Error) => void;
    const pending = new Promise<Response>((_, reject) => {
      fail = reject;
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL) =>
        String(url).includes("/assistant/chat")
          ? pending
          : new Response(JSON.stringify({ data: { stocks: [], themes: [] } }), { status: 200 }),
      ),
    );
    fireEvent.click(screen.getByLabelText("新建会话"));
    await act(async () => {});
    await send("会话二的问题");

    // 切回会话一
    fireEvent.click(screen.getByTestId("assistant-history-toggle"));
    fireEvent.click(screen.getByTestId("assistant-history-item"));
    await act(async () => {});
    expect(screen.getByText(/会话一的回答/)).toBeTruthy();

    // 失败此刻才到达：不得把"请求失败"写进会话一的最后一条
    await act(async () => {
      fail(new Error("network down"));
    });
    expect(screen.queryByText(/请求失败/)).toBeNull();
    expect(screen.getByText(/会话一的回答/)).toBeTruthy();
    expect(JSON.stringify(loadSessions(localStorage))).toBe(before);
  });

  it("记住的会话已不在（被淘汰/删除）→ 落到最新的一条，而不是空白", async () => {
    const mk = (id: string, at: number, text: string) => ({
      id,
      title: text,
      updatedAt: at,
      messages: [
        { id: 1, role: "user" as const, content: text, status: "ok" as const },
        { id: 2, role: "assistant" as const, content: `${text}的答复`, status: "ok" as const },
      ],
    });
    localStorage.setItem(
      SESSIONS_KEY,
      JSON.stringify([mk("old", 1, "旧会话"), mk("new", 2, "新会话")]),
    );
    // 上界只有 10 条 ⇒ "记住的 id 已经不在了"是常态（被淘汰或被删），必须能兜住
    localStorage.setItem(CURRENT_KEY, "已被删掉的 id");

    render(<FloatingAssistant />);
    await act(async () => {
      openPanel();
    });
    expect(screen.getByText(/新会话的答复/)).toBeTruthy();
    expect(screen.queryByText(/旧会话的答复/)).toBeNull();
  });

  it("刷新后继续对话：新消息 id 不与已恢复的撞 key", async () => {
    const s1 = makeStream();
    stubChat(s1.body);
    const view = render(<FloatingAssistant />);
    await act(async () => {
      openPanel();
    });
    await askAndFinish(s1, "先问一句", "先答一句");
    await waitFor(() => expect(loadSessions(localStorage)).toHaveLength(1)); // 落盘确认后再"刷新"

    view.unmount();
    render(<FloatingAssistant />);
    await act(async () => {
      openPanel();
    });
    // 恢复的消息带的是原来的 id（1/2）。若 id 计数器不越过最大值，新消息会拿到 1/2，
    // 与已恢复的消息**撞 key** —— 症状是问答串位，且只在刷新后出现。
    // 判据直接读渲染出来的 id 集合（不依赖 React 的告警文案：实测本环境不发告警）。
    const s2 = makeStream();
    stubChat(s2.body);
    await askAndFinish(s2, "再问一句", "再答一句");
    const ids = Array.from(document.querySelectorAll("[data-msg-id]")).map((el) =>
      el.getAttribute("data-msg-id"),
    );
    expect(ids.length).toBeGreaterThanOrEqual(4); // 恢复的 2 条 + 新增的 2 条
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("切走后重新发问：被弃的流结束时不得关掉新流的“生成中”", async () => {
    const s1 = makeStream();
    stubChat(s1.body);
    render(<FloatingAssistant />);
    await act(async () => {
      openPanel();
    });
    await askAndFinish(s1, "会话一的问题", "会话一的回答");
    await waitFor(() => expect(loadSessions(localStorage)).toHaveLength(1));

    // 会话二：起一个流，故意不收
    const s2 = makeStream();
    stubChat(s2.body);
    fireEvent.click(screen.getByLabelText("新建会话"));
    await act(async () => {});
    await send("会话二的问题");

    // 切回会话一，并在会话一里重新发问（新流 s3 正在生成）
    fireEvent.click(screen.getByTestId("assistant-history-toggle"));
    fireEvent.click(screen.getByTestId("assistant-history-item"));
    await act(async () => {});
    const s3 = makeStream();
    stubChat(s3.body);
    await send("会话一追问");

    // 被弃的 s2 此刻才结束 —— 它的收尾不得把 s3 的"生成中"关掉
    // （否则用户看到停止按钮凭空消失、光标停住，而生成其实还在跑）
    await act(async () => {
      s2.emit({ type: "delta", text: "会话二的半截回答" });
      s2.emit({ type: "done" });
      s2.close();
    });
    expect(screen.getByLabelText("停止生成")).toBeTruthy();

    await act(async () => {
      s3.emit({ type: "done" });
      s3.close();
    });
  });

  it("弃掉的流不再继续消费：切走后最多再读一次就退出读循环", async () => {
    const s1 = makeStream();
    stubChat(s1.body);
    render(<FloatingAssistant />);
    await act(async () => {
      openPanel();
    });
    await askAndFinish(s1, "会话一的问题", "会话一的回答");
    await waitFor(() => expect(loadSessions(localStorage)).toHaveLength(1));

    // 会话二：换成一个**可计数**的流，且只喂半截事件（永远凑不出一个完整事件）
    let reads = 0;
    let push!: (s: string) => void;
    const body = {
      getReader() {
        return {
          read: () => {
            reads += 1;
            return new Promise<{ done: boolean; value?: Uint8Array }>((resolve) => {
              push = (s) => resolve({ done: false, value: enc.encode(s) });
            });
          },
        };
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string | URL) =>
        String(url).includes("/assistant/chat")
          ? ({ ok: true, body } as unknown as Response)
          : new Response(JSON.stringify({ data: { stocks: [], themes: [] } }), { status: 200 }),
      ),
    );
    fireEvent.click(screen.getByLabelText("新建会话"));
    await act(async () => {});
    await send("会话二的问题");
    await act(async () => {
      push('data: {"type":"delta"'); // 半截：没有 \n\n ⇒ 这一轮什么也不处理
    });
    const at = reads;
    expect(at).toBeGreaterThanOrEqual(1); // 流确实在被消费

    // 切走 → 弃掉这条流
    fireEvent.click(screen.getByTestId("assistant-history-toggle"));
    fireEvent.click(screen.getByTestId("assistant-history-item"));
    await act(async () => {});

    // 再喂一段：读到它之后守卫必须立刻退出循环，而不是继续 read 下去
    // （少了顶部守卫，这条"早已没人要"的流会被无限消费下去）
    await act(async () => {
      push("data: more");
    });
    expect(reads - at).toBe(0);
  });

  it("删除会话：从列表与库里移除；删的是当前会话则原地换新", async () => {
    const s1 = makeStream();
    stubChat(s1.body);
    render(<FloatingAssistant />);
    await act(async () => {
      openPanel();
    });
    await askAndFinish(s1, "要被删掉的问题", "要被删掉的回答");
    await waitFor(() => expect(loadSessions(localStorage)).toHaveLength(1));

    fireEvent.click(screen.getByTestId("assistant-history-toggle"));
    fireEvent.click(screen.getByLabelText(/^删除会话：/));
    await act(async () => {});

    expect(loadSessions(localStorage)).toHaveLength(0);
    expect(screen.queryByText(/要被删掉的回答/)).toBeNull();
    // 列表空了 ⇒ 历史视图自动收起（入口按钮只在"有会话"时渲染，留着会无处可退）
    expect(screen.queryByTestId("assistant-history")).toBeNull();
    expect(screen.queryByTestId("assistant-history-toggle")).toBeNull();
  });
});


describe("assistant keyboard context", () => {
  it("keeps the same content through the inert exit and removes it after 160ms", async () => {
    vi.useFakeTimers();
    try {
      vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({data: [], meta: {}}), {headers:{"content-type":"application/json"}})));
      render(<FloatingAssistant />);
      await act(async () => openPanel());
      const panel = screen.getByTestId("assistant-panel");
      const input = screen.getByTestId("assistant-input");
      fireEvent.change(input, {target:{value:"保留中的草稿"}});
      fireEvent.click(screen.getByRole("button", {name:"最小化"}));
      expect(screen.queryByRole("region", {name:"AI 助手面板"})).toBeNull();
      expect(panel.hasAttribute("inert")).toBe(true);
      expect(panel.contains(input)).toBe(true);
      expect((input as HTMLTextAreaElement).value).toBe("保留中的草稿");
      expect(document.activeElement).toBe(screen.getByTestId("assistant-ball"));
      await act(async () => vi.advanceTimersByTime(159));
      expect(screen.getByTestId("assistant-panel")).toBe(panel);
      await act(async () => vi.advanceTimersByTime(1));
      expect(screen.queryByTestId("assistant-panel")).toBeNull();
      await act(async () => openPanel());
      expect((screen.getByTestId("assistant-input") as HTMLTextAreaElement).value).toBe("保留中的草稿");
    } finally {vi.useRealTimers();}
  });

  it("minimizing aborts the invisible dictionary read while the explicitly started answer continues", async () => {
    const stream = makeStream();
    const dictionarySignals: AbortSignal[] = [];
    let chatSignal!: AbortSignal;
    vi.stubGlobal("fetch", vi.fn((url: string | URL, init?: RequestInit) => {
      if (String(url).includes("/assistant/chat")) {
        chatSignal = init?.signal as AbortSignal;
        return Promise.resolve(new Response(stream.body, {headers:{"content-type":"text/event-stream"}}));
      }
      dictionarySignals.push(init?.signal as AbortSignal);
      return new Promise<Response>(() => {});
    }));
    render(<FloatingAssistant />);
    await act(async () => openPanel());
    await send("继续已启动的只读解释");
    fireEvent.click(screen.getByRole("button", {name:"最小化"}));
    expect(dictionarySignals[0].aborted).toBe(true);
    expect(chatSignal.aborted).toBe(false);
    expect(screen.queryByRole("textbox")).toBeNull(); // Retained exit content is inert and inaccessible.
    await act(async () => {stream.emit({type:"delta", text:"原来的解释完成"}); stream.emit({type:"done"}); stream.close();});
    await act(async () => openPanel());
    expect(screen.getByText("原来的解释完成")).toBeTruthy();
    expect(dictionarySignals).toHaveLength(2);
  });

  it("opens from keyboard, focuses input, and returns focus on Escape without posting", async () => {
    const fetchMock = vi.fn(async (_url: string | URL) => new Response(JSON.stringify({data: [], meta: {}}), {headers: {"content-type":"application/json"}}));
    vi.stubGlobal("fetch", fetchMock);
    render(<FloatingAssistant />);
    const ball = await screen.findByTestId("assistant-ball"); ball.focus();
    fireEvent.keyDown(ball, {key:"Enter"});
    expect(document.activeElement).toBe(await screen.findByTestId("assistant-input"));
    fireEvent.keyDown(document.activeElement!, {key:"Escape"});
    expect(screen.queryByRole("region", {name:"AI 助手面板"})).toBeNull();
    expect(screen.queryByRole("textbox")).toBeNull();
    const shell = screen.getByTestId("assistant-panel");
    expect(shell.hasAttribute("inert")).toBe(true);
    expect(shell.getAttribute("data-motion-state")).toBe("closed");
    expect(document.activeElement).toBe(ball);
    expect(fetchMock.mock.calls.some(call => String(call[0]).includes("/assistant/chat"))).toBe(false);
  });
});
