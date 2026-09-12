import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { KbBrowserTab } from "./kb-browser-tab";

// 分层呈现（2026-09-12）的**契约测试**。
//
// 背景：本次改动的动机是「79 份平铺 ⇒ canonical 只占 11 份、22 份归档件与 13 份
// 逐日日志与它并列，用户分不清『现行规则』与『历史结论』」。修法是把归档 + 时间序列
// 折进「历史与日志」，默认不渲染。
//
// 但折叠自带一个**反向风险**：内容被藏起来 ⇒ 用户搜不到 ⇒ 「全量可查」的承诺失效。
// 因此本组测试锁两条**方向相反**的契约：
//   ① 默认必须折（否则改动等于没做，噪音原样回来）；
//   ② 搜索必须穿透折叠（否则为了整洁牺牲了检索，得不偿失）。
// 任何一条单独被"优化"掉都会让另一条失效——这正是它们必须同时存在的理由。

// ⚠️ fixture 必须走 `vi.hoisted`：`vi.mock` 的工厂被提升到 import 之前执行，
// 直接引用模块顶层 `const` 会撞 "Cannot access before initialization"。
const { FILES } = vi.hoisted(() => {
  const mk = (path: string, name: string, dir: string, tier: string, kb_ids: string[]) => ({
    path,
    name,
    dir,
    tier,
    size: 128,
    kb_ids,
  });
  return {
    FILES: [
      mk("kb/00-INDEX.md", "00-INDEX.md", "kb", "canonical", ["KB-STOCK-1"]),
      mk("kb/03-engineering.md", "03-engineering.md", "kb", "canonical", ["KB-ENG-60"]),
      mk("INDEX.md", "INDEX.md", "docs", "current", []),
      mk("retro-and-gaps.md", "retro-and-gaps.md", "docs", "current", ["KB-ENG-36"]),
      mk("summary/architecture-design.md", "architecture-design.md", "summary", "current", []),
      mk("archive/plan-review.md", "plan-review.md", "archive", "history", []),
      mk("daily-review/2026-09-11.md", "2026-09-11.md", "daily-review", "timeline", []),
    ],
  };
});

vi.mock("@/lib/api", () => ({
  getAgentKbTree: vi.fn(async () => ({ root: "/repo/docs", files: FILES })),
  getAgentKbFile: vi.fn(async (path: string) => ({ path, content: `# ${path}` })),
}));

/** 左栏里当前渲染出的文档条目按钮（按 `.md` 后缀识别，与真实渲染口径一致） */
const docRows = () =>
  [...document.querySelectorAll("aside button")].filter((b) =>
    /\.md$/.test((b.textContent ?? "").replace(/KB\d+$/, "").trim()),
  );

/** 折叠区容器：用 aria-controls 定位，避免依赖 class */
const historyList = () => document.getElementById("kb-history-list");

const toggle = () => screen.getByRole("button", { name: /历史与日志/ });

async function mount() {
  render(<KbBrowserTab />);
  await waitFor(() => expect(docRows().length).toBeGreaterThan(0));
}

beforeEach(() => {
  vi.clearAllMocks();
});
afterEach(cleanup);

describe("知识库面板 · 分层折叠（2026-09-12）", () => {
  it("默认只渲染 canonical + 现役，归档与逐日日志折起不占版面", async () => {
    await mount();

    // fixture 共 7 份，其中 2 份历史（archive 1 + timeline 1）应默认不渲染
    expect(docRows()).toHaveLength(5);
    expect(screen.queryByText("plan-review.md")).toBeNull();
    expect(screen.queryByText("2026-09-11.md")).toBeNull();

    // 折叠入口必须报出被折起的总数，否则用户不知道「还有东西没看到」
    expect(toggle().textContent).toContain("历史与日志 · 2 份");
    expect(toggle().getAttribute("aria-expanded")).toBe("false");
  });

  it("展开后历史条目全部可见，且分组计数与实际条目一致", async () => {
    await mount();
    fireEvent.click(toggle());

    expect(docRows()).toHaveLength(7);
    expect(toggle().getAttribute("aria-expanded")).toBe("true");
    expect(screen.getByText("plan-review.md")).toBeTruthy();
    expect(screen.getByText("2026-09-11.md")).toBeTruthy();

    // 归档件与其所属目录标签同时出现——用户要知道自己看的是历史，而不是现行规则
    expect(historyList()?.textContent).toContain("archive · 1");
    expect(historyList()?.textContent).toContain("daily-review · 1");
    expect(historyList()?.textContent).toContain("只读历史");
  });

  it("默认展开态下仍留一个「只读历史」警示，防止历史条目被当现行规则引用", async () => {
    // 本条针对折叠的**语义**而非可见性：展开本身就意味着用户要读归档件，
    // 此时不给「引用前先确认未过时」的提示，等于让归档件伪装成现行规则。
    await mount();
    fireEvent.click(toggle());
    expect(historyList()?.textContent).toContain("引用前先确认未过时");
  });
});

describe("知识库面板 · 搜索穿透折叠", () => {
  it("搜索只在归档中存在的文档时，折叠区自动展开并命中", async () => {
    // 这是折叠方案最大的风险点：默认不渲染 ⇒ 若搜索也只看已渲染集合，
    // 用户搜「plan-review」会得到空结果，进而认定文档不存在。
    await mount();
    expect(screen.queryByText("plan-review.md")).toBeNull();

    fireEvent.change(screen.getByPlaceholderText("搜文档 / KB-ID"), {
      target: { value: "plan-review" },
    });

    await waitFor(() => expect(docRows()).toHaveLength(1));
    expect(screen.getByText("plan-review.md")).toBeTruthy();
    // 搜索态下折叠按钮应转为不可点（已自动展开），避免出现"点了没反应"的死控件
    expect((toggle() as HTMLButtonElement).disabled).toBe(true);
  });

  it("搜索 KB-ID 能同时命中知识库层与现役层，并按层分组", async () => {
    await mount();
    fireEvent.change(screen.getByPlaceholderText("搜文档 / KB-ID"), {
      target: { value: "KB-ENG-60" },
    });

    await waitFor(() => expect(docRows()).toHaveLength(1));
    expect(screen.getByText("03-engineering.md")).toBeTruthy();
    // 分层标题在搜索结果中保留——命中来自哪一层是判断可信度的关键信息
    expect(screen.getByText(/知识库（唯一权威）· 1/)).toBeTruthy();
  });

  it("无匹配时的提示必须声明「含历史与日志」，否则用户会误判检索范围", async () => {
    await mount();
    fireEvent.change(screen.getByPlaceholderText("搜文档 / KB-ID"), {
      target: { value: "zzz-no-such-doc" },
    });

    await waitFor(() => expect(screen.getByText("无匹配文档（含历史与日志）")).toBeTruthy());
    expect(docRows()).toHaveLength(0);
  });
});
