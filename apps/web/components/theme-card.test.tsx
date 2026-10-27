import { afterEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { ThemeCardView } from "@/components/theme-card";
import { JUMP_PILL_CLASS } from "@/components/ui/jump-link";
import type { ThemeStrengthRow } from "@/lib/api";
import type { LadderRung, ThemeCard, ThemeBoardCandidate } from "@/types/market";

// 题材卡内的个股跳转依赖 useRouter，测试环境需桩掉（同 search-box.test.tsx）
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn() }) }));

// 展开行内嵌的介入条件清单自带取数，入口测试只关心「按钮 ↔ 展开行」的联动，
// 因此桩成纯展示组件，避免在单测里发请求（清单自身行为见 entry-checklist.test.tsx）
vi.mock("@/components/concept-detail-modal",()=>({ConceptDetailModal:({code,catalogKind,referenceDate,active}: {code:string;catalogKind:string;referenceDate?:string;active:boolean})=>active?<div data-testid="official-detail">{catalogKind}:{code}:{referenceDate}</div>:null}));

vi.mock("@/components/entry-checklist", () => ({
  EntryChecklist: ({ symbol, active = true }: { symbol: string; active?: boolean }) => (
    <div data-testid="stub-checklist" data-active={String(active)}>{symbol}</div>
  ),
}));

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动
afterEach(() => {cleanup(); vi.useRealTimers();});

it("opens each rung cause for reading without adding a wide table column", async () => {
  const rung: LadderRung = {symbol: "600825", name: "新华传媒", role: "龙头", is_primary: true,
    other_themes: ["传媒成员标签不是原因"], boards: 4, reason: "拟收购财联社+重大资产重组", source: "ths", trade_date: "2026-10-09"};
  const {container} = render(<ThemeCardView card={card({ladder: [rung]})} rank={1} />);
  fireEvent.click(screen.getByRole("button", {name: /涨停原因 · 查看依据/}));
  const dialog = await screen.findByRole("dialog", {name: "涨停原因原文"});
  expect(within(dialog).getByText("拟收购财联社+重大资产重组")).toBeTruthy();
  expect(within(dialog).getByText("2026-10-09")).toBeTruthy();
  expect(within(dialog).getByLabelText("数据源归因原文").textContent).toContain("同花顺");
  expect(container.querySelectorAll("thead th")).toHaveLength(9);
});

it("keeps ladder space and independent controls from activating an enclosing card", () => {
  const enclosing = vi.fn();
  render(<div onClick={enclosing}><ThemeCardView card={card({ladder: [rung()]})} rank={1} /></div>);
  fireEvent.click(screen.getByText("09:35"));
  expect(enclosing).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("link", {name: /新赛股份/}));
  expect(enclosing).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", {name: "查看"}));
  expect(screen.getByTestId("stub-checklist").textContent).toBe("600540");
  expect(enclosing).not.toHaveBeenCalled();
});

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
    board_matched: false,
    exact_catalog: {code: "885333.TI", name: "商业航天", source: "ths_official", basis: "exact_name",kind:"concept",catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00"},
    catalog_mapping_state:"exact",
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
    code: "885333.TI",
    name: "商业航天",
    coverage: {member_total: 482, sampled: 200, not_sampled: 282, quote_returned: 190, valid_change_count: 180, amount_count: 170, missing_amount: 30, sample_limit: 200, complete_members: false},
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
    board: null,
    candidate_board: candidate(),
    ...over,
  };
}

function candidate(over: Partial<ThemeBoardCandidate> = {}): ThemeBoardCandidate {
  return {board_code: "BK1234", name: "商业航天", kind: "concept", source: "eastmoney", source_product: "legacy_clist",
    requested_name: "商业航天", relationship: "name_candidate", match_method: "exact_name", same_concept_verified: false,
    change_pct: -1.5, main_net_yi: -21.3, main_net_ratio: -2, streak: 2, ...over};
}

describe("题材卡 · 正式身份、样本与名称候选", () => {
  it("常显候选身份并保留东财代码，净额不成为正式题材资金", () => {
    render(<ThemeCardView card={card()} rank={1} strength={strength()} />);
    const badge = screen.getByTitle(/BK1234.*身份未核/);
    expect(badge.textContent).toContain("东财候选 · 身份未核");
    expect(badge.textContent).toContain("-21.3亿");
    expect(badge.textContent).toContain("BK1234");
    expect(badge.getAttribute("title")).toContain("不参与同花顺资金核验");
    expect(screen.queryByText("-1.50%")).toBeNull();
  });

  it("首屏披露采样与两个有效分母，不把样本成交额称为完整题材额", () => {
    render(<ThemeCardView card={card()} rank={1} strength={strength()} />);
    const badge = screen.getByTitle(/官方成分批量快照聚合/);
    expect(badge.textContent).toContain("样本200/482");
    expect(badge.textContent).toContain("涨幅180/200");
    expect(badge.textContent).toContain("金额170/200");
    expect(badge.textContent).toContain("样本成交额");
    expect(badge.getAttribute("title")).toContain("未采样282");
  });

  it("缺失金额显示待核；真实零成交额和零净额仍保留", () => {
    const {unmount} = render(<ThemeCardView card={card()} rank={1} strength={strength({total_amount: null, candidate_board: candidate({main_net_yi: null})})} />);
    expect(screen.getByTitle(/官方成分批量快照聚合/).textContent).toContain("样本成交额 待核");
    expect(screen.getByTitle(/BK1234.*身份未核/).textContent).toContain("净额待核");
    unmount();
    render(<ThemeCardView card={card()} rank={1} strength={strength({total_amount: 0, candidate_board: candidate({main_net_yi: 0})})} />);
    expect(screen.getByTitle(/官方成分批量快照聚合/).textContent).not.toContain("成交额 待核");
    expect(screen.getByTitle(/BK1234.*身份未核/).textContent).not.toContain("净额待核");
  });

  it("旧 board 返回值也不能升级为已确认涨幅或资金证据", () => {
    render(<ThemeCardView card={card({board: {name: "同名旧板块", board_code: "BK0001", change_pct: 9.99, main_net_inflow: 777e8}})} rank={1}
      strength={strength({candidate_board: null, board: {board_code: "BK0001", name: "同名旧板块", kind: "concept", change_pct: 9.99, main_net_yi: 777, main_net_ratio: 1, streak: 3}})} />);
    expect(screen.queryByText(/777.*亿|9.99%|同名旧板块/)).toBeNull();
  });

  it("仅正式同花顺区间独立展示源日期与风险用途，不借用候选涨幅", () => {
    render(<ThemeCardView card={card({official_interval: {code: "885333.TI", name: "商业航天", source: "ths_official_kline",kind:"concept",catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00", state: "verified", verified: true,
      chg_3d: 3.25, chg_5d: null, chg_10d: 6.5, source_as_of: "2026-10-26", requested_trade_date: "2026-10-27"}})} rank={1} strength={strength()} />);
    fireEvent.click(screen.getByRole("tab", {name: /强度指标与板块表现/}));
    expect(screen.getByText("同花顺区间 · 仅作风险参照")).toBeTruthy();
    expect(screen.getByText(/885333.TI.*日线日期 2026-10-26.*同源区间已核/)).toBeTruthy();
    expect(screen.getByText("+3.25%")).toBeTruthy();
    expect(screen.queryByText("已交叉验证")).toBeNull();
  });

  it("当日日线标签不证明已完成或价格已核，只作同源区间风险参照", () => {
    const {container} = render(<ThemeCardView card={card({official_interval: {code: "885333.TI", name: "商业航天", source: "ths_official_kline",kind:"concept",catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00", state: "verified", verified: true,
      chg_3d: 3.25, chg_5d: 4, chg_10d: 6.5, source_as_of: "2026-10-27", requested_trade_date: "2026-10-27"}})} rank={1} />);
    fireEvent.click(screen.getByRole("tab", {name: /强度指标与板块表现/}));
    expect(screen.getByText(/日线日期 2026-10-27.*同源区间已核/)).toBeTruthy();
    expect(container.textContent).not.toMatch(/完成日|已完成|收盘已核|价格已核/);
    expect(screen.getAllByTitle(/可能含当日形成条，仅风险参考/)).toHaveLength(3);
  });

  it("区间代码不等于正式身份时不借同名展示，错接样本也不显示", () => {
    render(<ThemeCardView card={card({official_interval: {code: "885999.TI", name: "商业航天", source: "ths_official_kline",kind:"concept",catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00", state: "verified", verified: true,
      chg_3d: 19.99, chg_5d: 29.99, chg_10d: 39.99, source_as_of: "2026-10-26", requested_trade_date: "2026-10-27"}})} rank={1}
      strength={strength({code: "885999.TI", candidate_board: null})} />);
    fireEvent.click(screen.getByRole("tab", {name: /强度指标与板块表现/}));
    expect(screen.queryByText(/19.99|29.99|39.99/)).toBeNull();
    expect(screen.queryByTitle(/官方成分批量快照聚合/)).toBeNull();
  });

  it("候选来自卡片且无合力时也明确身份未核，资金缺失为中性待核", () => {
    render(<ThemeCardView card={card({candidate_board: candidate({main_net_yi: null})})} rank={1} strength={null} />);
    expect(screen.getByTitle(/BK1234.*身份未核/).textContent).toContain("净额待核");
    const status = screen.getByText("资金待核");
    expect(status.className).not.toMatch(/rose|red|text-up/);
  });

  it("无合力响应时保留题材与既有梯队", () => {
    render(<ThemeCardView card={card()} rank={1} strength={null} />);
    expect(screen.queryByTitle(/官方成分批量快照聚合/)).toBeNull();
    expect(screen.getByRole("heading", {name: "商业航天"})).toBeTruthy();
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

    vi.useFakeTimers();
    fireEvent.click(screen.getByRole("button", { name: "收起" }));
    expect(screen.getByTestId("stub-checklist").closest("[inert]")).toBeTruthy();
    expect(screen.getByTestId("stub-checklist").dataset.active).toBe("false");
    act(() => vi.advanceTimersByTime(160));
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
    render(<ThemeCardView card={card({exact_catalog:null,catalog_mapping_state:"unmapped"})} rank={1} strength={null} />);
    expect(screen.queryByRole("button", { name: "成分 ↗" })).toBeNull();
  });

  it("「涨停池 ↗」是链接且同款 pill 样式", () => {
    render(<ThemeCardView card={card()} rank={1} strength={null} />);

    const link = screen.getByRole("link", { name: "涨停池 ↗" });
    expect(link.className).toBe(JUMP_PILL_CLASS);
    expect(link.getAttribute("href")).toContain("limitup");
  });
});


it("行业映射走同类成分，不套概念合力或未核代码",()=>{
  const value=card({theme:"汽车零部件",catalog_code:"885999.TI",exact_catalog:{code:"881126.TI",name:"汽车零部件",source:"ths_official",basis:"exact_name",kind:"industry",catalog_version:"b".repeat(64),observed_at:"2026-10-27T12:00:00+08:00"}});
  render(<ThemeCardView card={value} rank={1} tradeDate="2026-10-23" strength={strength({code:"881126.TI"})}/>);
  expect(screen.getByText("行业合力分析尚未覆盖")).toBeTruthy();
  expect(screen.queryByTitle(/官方成分批量快照聚合/)).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"成分 ↗"}));
  expect(screen.getByTestId("official-detail").textContent).toBe("industry:881126.TI:2026-10-23");
});
it("映射原名不符时不套同码指标，也不开放catalog_code入口",()=>{
  const base=card();
  render(<ThemeCardView card={card({exact_catalog:{...base.exact_catalog!,name:"其他来源标签"},catalog_code:"885333.TI"})} rank={1} strength={strength()}/>);
  expect(screen.queryByRole("button",{name:"成分 ↗"})).toBeNull();
  expect(screen.queryByTitle(/官方成分批量快照聚合/)).toBeNull();
});


it("区间kind或目录版本与正式身份不符时不借同码显示",()=>{
  render(<ThemeCardView card={card({official_interval:{code:"885333.TI",name:"商业航天",source:"ths_official_kline",kind:"industry",catalog_version:"b".repeat(64),observed_at:"2026-10-27T12:00:00+08:00",state:"verified",verified:true,chg_3d:19.99,chg_5d:29.99,chg_10d:39.99,source_as_of:"2026-10-26",requested_trade_date:"2026-10-27"}})} rank={1}/>);
  fireEvent.click(screen.getByRole("tab",{name:/强度指标与板块表现/}));
  expect(screen.queryByText(/19.99|29.99|39.99/)).toBeNull();
});


it("历史卡仅旁列当前目录，正确行业导航不套历史合力或区间",()=>{
  const historical=card({theme:"汽车零部件",exact_catalog:null,catalog_mapping_state:"unavailable",catalog_mapping_context:{reason:"historical_identity_not_frozen"},catalog_code:null,official_interval:{code:"881126.TI",name:"汽车零部件",source:"ths_official_kline",kind:"industry",catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00",state:"verified",verified:true,chg_3d:19.99,chg_5d:29.99,chg_10d:39.99,source_as_of:"2026-10-23",requested_trade_date:"2026-10-23"}});
  const reference={scope:"current_directory_reference" as const,catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00",items:[{theme:"汽车零部件",catalog_mapping_state:"exact" as const,exact_catalog:{code:"881126.TI",name:"汽车零部件",source:"ths_official",basis:"exact_name",kind:"industry" as const,catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00"}}]};
  const before=JSON.stringify(historical);
  const view=render(<ThemeCardView card={historical} rank={1} tradeDate="2026-10-23" currentThemeCatalog={reference} strength={strength({code:"881126.TI"})}/>);
  expect(screen.getByRole("heading",{name:"汽车零部件"})).toBeTruthy();
  expect(screen.getByText("现目录同名参考 · 881126.TI")).toBeTruthy();
  expect(screen.getByText("原标签 · 当时未记录映射")).toBeTruthy();
  expect(screen.queryByText("目录未就绪 · 保留原标签")).toBeNull();
  expect(screen.queryByText("同花顺 · 881126.TI")).toBeNull();
  expect(screen.queryByRole("button",{name:"成分 ↗"})).toBeNull();
  expect(screen.queryByTitle(/官方成分批量快照聚合/)).toBeNull();
  fireEvent.click(screen.getByRole("tab",{name:/强度指标与板块表现/}));
  expect(screen.queryByText(/19.99|29.99|39.99/)).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"当前成分 ↗"}));
  expect(screen.getByTestId("official-detail").textContent).toBe("industry:881126.TI:2026-10-23");
  expect(JSON.stringify(historical)).toBe(before);
  view.rerender(<ThemeCardView card={historical} rank={1} tradeDate="2026-10-22" currentThemeCatalog={reference}/>);
  expect(screen.queryByTestId("official-detail")).toBeNull();
  view.rerender(<ThemeCardView card={historical} rank={1} tradeDate="2026-10-23" currentThemeCatalog={reference}/>);
  expect(screen.queryByTestId("official-detail")).toBeNull();
});
it("历史主标签没有同名对应时不套其子标签的当前代码",()=>{
  const reference={scope:"current_directory_reference" as const,catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00",items:[{theme:"羊字辈",catalog_mapping_state:"unmapped" as const,exact_catalog:null},{theme:"冷链物流",catalog_mapping_state:"exact" as const,exact_catalog:{code:"885502.TI",name:"冷链物流",source:"ths_official",basis:"exact_name",kind:"concept" as const,catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00"}}]};
  render(<ThemeCardView card={card({theme:"羊字辈",raw_tags:["冷链物流"],exact_catalog:null,catalog_mapping_state:undefined})} rank={1} tradeDate="2026-10-23" currentThemeCatalog={reference}/>);
  expect(screen.getByText("当前目录无同名对应")).toBeTruthy();
  expect(screen.queryByRole("button",{name:"当前成分 ↗"})).toBeNull();
});
