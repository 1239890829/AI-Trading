import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ThemeCardView } from "@/components/theme-card";
import { JUMP_PILL_CLASS } from "@/components/ui/jump-link";
import type { ThemeStrengthRow } from "@/lib/api";
import type { LadderRung, ThemeCard } from "@/types/market";

// 题材卡内的个股跳转依赖 useRouter，测试环境需桩掉（同 search-box.test.tsx）
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

// 展开行内嵌的介入条件清单自带取数，入口测试只关心「按钮 ↔ 展开行」的联动，
// 因此桩成纯展示组件，避免在单测里发请求（清单自身行为见 entry-checklist.test.tsx）
vi.mock("@/components/entry-checklist", () => ({
  EntryChecklist: ({ symbol }: { symbol: string }) => (
    <div data-testid="stub-checklist">{symbol}</div>
  ),
}));

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动
afterEach(cleanup);

function card(over: Partial<ThemeCard> = {}): ThemeCard {
  return {
    theme: "商业航天",
    raw_tags: [],
    is_unclassified: false,
    strength_score: 82,
    strength_tier: "强势",
    tier_basis: "涨停家数+高度",
    sort_basis: "按强度分降序",
    stage: "发酵",
    stage_basis: [],
    formation: "成建制",
    health_note: "梯队完整",
    risks: [],
    board_matched: true,
    performance: {
      limit_up_count: 3,
      max_boards: 4,
      echelon_levels: {},
      echelon_completeness: 1,
      has_succession: true,
      reopen_rate: 0.1,
      seal_success_rate: 0.8,
      seal_time_distribution: {},
      seal_quality: 0.9,
      has_middle_weight: true,
      active_days: 3,
      daily_limit_up_counts: [],
      premium_samples: 0,
    },
    ladder: [],
    leaders: { main: null, middle_weights: [], candidates: [] },
    ...over,
  };
}

function strength(over: Partial<ThemeStrengthRow> = {}): ThemeStrengthRow {
  return {
    name: "商业航天",
    count: 200,
    up: 46,
    down: 151,
    flat: 3,
    missing: 0,
    limit_up_count: 3,
    avg_change_pct: -1.2,
    total_amount: 1.1e11,
    top_gainers: [],
    basis: "官方成分批量快照聚合",
    board: {
      board_code: "BK1234",
      name: "商业航天",
      kind: "concept",
      change_pct: -1.5,
      main_net_yi: -21.3,
      main_net_ratio: -2.0,
      streak: 2,
    },
    ...over,
  };
}

describe("题材卡 · 板块资金徽标（P1-5，东财 f62 口径）", () => {
  it("有 board 时并列渲染两套口径（合力 + 板块资金），金额带符号", () => {
    render(<ThemeCardView card={card()} rank={1} strength={strength()} />);
    // 合力（ths 成分快照聚合）照旧
    expect(screen.getByTitle(/官方成分批量快照聚合/)).toBeTruthy();
    // 板块资金（东财 f62）
    const badge = screen.getByTitle(/f62 主力净额口径/);
    expect(badge.textContent).toContain("商业航天");
    expect(badge.textContent).toContain("-21.3亿");
    // 口径分离：标题里必须写明不可与合力相加
    expect(badge.getAttribute("title")).toContain("不可相加");
  });

  it("board 为 null 时不渲染板块徽标（三态：匹配不到就不显示，不占位不臆造）", () => {
    render(<ThemeCardView card={card()} rank={1} strength={strength({ board: null })} />);
    expect(screen.queryByTitle(/f62 主力净额口径/)).toBeNull();
    // 合力徽标不受影响
    expect(screen.getByTitle(/官方成分批量快照聚合/)).toBeTruthy();
  });

  it("streak >= 1 才在 title 里给出「连续 N 日净流入」", () => {
    render(<ThemeCardView card={card()} rank={1} strength={strength()} />);
    expect(screen.getByTitle(/f62 主力净额口径/).getAttribute("title")).toContain("连续 2 日净流入");
  });

  it("streak = 0（今日转流出）与 null（未沉淀）都不显示连续天数", () => {
    const { unmount } = render(
      <ThemeCardView card={card()} rank={1} strength={strength({ board: { ...strength().board!, streak: 0 } })} />,
    );
    expect(screen.getByTitle(/f62 主力净额口径/).getAttribute("title")).not.toContain("日净流入");
    unmount();

    render(
      <ThemeCardView card={card()} rank={1} strength={strength({ board: { ...strength().board!, streak: null } })} />,
    );
    expect(screen.getByTitle(/f62 主力净额口径/).getAttribute("title")).not.toContain("日净流入");
  });

  it("金额缺失（null）如实显示 --，不冒充 0", () => {
    render(
      <ThemeCardView
        card={card()}
        rank={1}
        strength={strength({ board: { ...strength().board!, main_net_yi: null } })}
      />,
    );
    expect(screen.getByTitle(/f62 主力净额口径/).textContent).toContain("--");
  });

  it("无 strength（合力取数失败）时两套徽标都不渲染，卡片其余部分照常", () => {
    render(<ThemeCardView card={card()} rank={1} strength={null} />);
    expect(screen.queryByTitle(/f62 主力净额口径/)).toBeNull();
    expect(screen.queryByTitle(/官方成分批量快照聚合/)).toBeNull();
    expect(screen.getByText("商业航天")).toBeTruthy();
  });
});

function rung(over: Partial<LadderRung> = {}): LadderRung {
  return {
    symbol: "600540",
    name: "新赛股份",
    role: "龙头",
    is_primary: true,
    other_themes: [],
    boards: 4,
    seal_amount: 1.2e8,
    break_count: 0,
    turnover_rate: 8.5,
    float_market_cap: 5.0e9,
    first_seal_time: "09:35",
    seal_phase: "早盘",
    ...over,
  };
}

describe("题材卡 · 介入条件入口（P1-13）", () => {
  it("默认收起；点「查看」展开对应标的的清单行", () => {
    render(<ThemeCardView card={card({ ladder: [rung()] })} rank={1} strength={null} />);

    // 收起态：不渲染清单、不请求
    expect(screen.queryByTestId("stub-checklist")).toBeNull();
    const btn = screen.getByRole("button", { name: "查看" });
    expect(btn.getAttribute("aria-expanded")).toBe("false");

    fireEvent.click(btn);
    const stub = screen.getByTestId("stub-checklist");
    expect(stub.textContent).toBe("600540");
    expect(screen.getByRole("button", { name: "收起" }).getAttribute("aria-expanded")).toBe("true");
  });

  it("再次点击收起该行（同时只展开一只，切换标的即换行）", () => {
    render(
      <ThemeCardView
        card={card({ ladder: [rung(), rung({ symbol: "000901", name: "航天科技", role: "中军" })] })}
        rank={1}
        strength={null}
      />,
    );

    const [first, second] = screen.getAllByRole("button", { name: "查看" });
    fireEvent.click(first);
    expect(screen.getByTestId("stub-checklist").textContent).toBe("600540");

    fireEvent.click(second);
    const stubs = screen.getAllByTestId("stub-checklist");
    expect(stubs).toHaveLength(1);
    expect(stubs[0].textContent).toBe("000901");

    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    expect(screen.queryByTestId("stub-checklist")).toBeNull();
  });
});

describe("题材卡 · 跳转入口可发现性（P1-18）", () => {
  // 起因（2026-09-09 审查）：「题材页↗ / 成分↗」原样式是 `text-[10px] text-zinc-600 dark:text-zinc-400`
  // 且无底色 —— 与灰色注释文字无异，用户扫过去认不出入口（反馈「可发现性偏弱」）。
  // 这两条断言锁的是**视觉三信号**（底色边界 / 字号 ≥11px / hover 反馈）而不是
  // 「用了哪个类名」：任何一个缺失，入口就退化成注释。

  it("「成分 ↗」与共享常量逐字一致（防某处偷偷用回旧样式）", () => {
    render(<ThemeCardView card={card({ catalog_code: "BK1234" })} rank={1} strength={null} />);

    const btn = screen.getByRole("button", { name: "成分 ↗" });
    expect(btn.className).toBe(JUMP_PILL_CLASS);
    // 三信号分解断言（常量被改坏时能直接指出缺哪个信号）
    expect(btn.className).toContain("text-[11px]");
    expect(btn.className).toContain("bg-zinc-50");
    expect(btn.className).toContain("hover:border-sky-400");
    // 旧样式特征必须消失（10px 灰字 + 无底色）
    expect(btn.className).not.toContain("text-[10px]");
  });

  it("无目录映射时不渲染「成分 ↗」（缺省不占位）", () => {
    render(<ThemeCardView card={card()} rank={1} strength={null} />);
    expect(screen.queryByRole("button", { name: "成分 ↗" })).toBeNull();
  });

  it("「涨停池 ↗」是链接且同款 pill 样式", () => {
    render(<ThemeCardView card={card()} rank={1} strength={null} />);

    const link = screen.getByRole("link", { name: "涨停池 ↗" });
    expect(link.className).toBe(JUMP_PILL_CLASS);
    expect(link.getAttribute("href")).toContain("limitup");
  });
});
