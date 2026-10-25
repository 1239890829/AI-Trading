import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type {SavedMethodRunDetail, SavedMethodRunIdentity, SavedStrategyRun, StrategyRegistryItem} from "@/lib/api/picks";

/**
 * 策略健康度面板（#11）。
 *
 * 三条渲染纪律，失守任何一条都会**误导用户**：
 * 1. 「判不出」（insufficient/thin/no_pipeline/…）绝不能看起来像"健康"；
 * 2. `basis` 口径必须显示（market_neutral 与 absolute 不可互相解释）；
 * 3. 核验结论缺失时如实说"尚无产物"，不得留白让人以为已验证。
 */
const healthMock = vi.fn();
const registryMock = vi.fn();
const runsMock = vi.fn();
const runMock = vi.fn();

vi.mock("@/lib/api", () => ({
  getStrategyHealth: () => healthMock(),
  getStrategyRegistry: () => registryMock(),
}));
vi.mock("@/lib/api/picks", async()=>({...await vi.importActual<typeof import("@/lib/api/picks")>("@/lib/api/picks"),
  getStrategyMethodRuns:(...args:unknown[])=>runsMock(...args), getStrategyMethodRun:(...args:unknown[])=>runMock(...args)}));

const { StrategyHealthTab } = await import("./strategy-health-tab");
beforeEach(()=>{
  window.history.replaceState({},"","/agent?area=research&tab=strategies");
  runsMock.mockImplementation(async({method}:{method:string})=>({method,items:[],coverage:{limit:30,possibly_truncated:false,history_semantics:"原保存范围"}}));
});

afterEach(() => {
  cleanup();
  healthMock.mockReset();
  registryMock.mockReset();
  runsMock.mockReset();runMock.mockReset();
});

describe("策略健康 · 独立读取", () => {
  const health = { strategies: [{ strategy_key: "k", name: "独立健康结果", status: "ok" }] };
  it("慢登记册不挡健康结果，核验尚未返回不能解释为未跑过", async () => {
    healthMock.mockResolvedValue(health);
    registryMock.mockReturnValue(new Promise(() => {}));
    render(<StrategyHealthTab />);
    expect(await screen.findByText(/独立健康结果/)).toBeTruthy();
    expect(screen.getByText(/登记册正在读取，核验结论未知/)).toBeTruthy();
    expect(screen.queryByText(/未跑过核验/)).toBeNull();
  });

  it("登记册失败不清健康主体，不能声称没有核验产物", async () => {
    healthMock.mockResolvedValue(health);
    registryMock.mockRejectedValue(new Error("registry offline"));
    render(<StrategyHealthTab />);
    expect(await screen.findByText(/独立健康结果/)).toBeTruthy();
    expect(screen.getByText(/核验登记册加载失败/)).toBeTruthy();
    expect(screen.queryByText(/未跑过核验/)).toBeNull();
  });

  it("健康刷新失败保留上次结果，恢复后清错误，旧登记册晚回不覆盖新版", async () => {
    let old!: (items: unknown[]) => void;
    healthMock.mockResolvedValueOnce(health).mockRejectedValueOnce(new Error("health offline")).mockResolvedValue(health);
    registryMock.mockResolvedValueOnce([]).mockReturnValueOnce(new Promise(resolve => { old = resolve; }))
      .mockResolvedValue([{ key: "k", verification: { available: true, verdict: "current verification" } }]);
    render(<StrategyHealthTab />);
    await screen.findByText(/独立健康结果/);
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(await screen.findByText(/策略健康加载失败.*上次读取结果/)).toBeTruthy();
    expect(screen.getByText(/独立健康结果/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "刷新" }));
    expect(await screen.findByText("current verification")).toBeTruthy();
    await act(async () => old([{ key: "k", verification: { available: true, verdict: "old verification" } }]));
    expect(screen.queryByText("old verification")).toBeNull();
    expect(screen.queryByText(/health offline/)).toBeNull();
  });
});

function stub(health: unknown, registry: unknown[] = []) {
  healthMock.mockResolvedValue(health);
  registryMock.mockResolvedValue(registry);
  const originals=registry as StrategyRegistryItem[];
  const saved=new Map(originals.filter(item=>item.runtime).map(item=>{
    const runtime=item.runtime as SavedStrategyRun;
    const identity:SavedMethodRunIdentity={method:item.key as SavedMethodRunIdentity["method"],run_id:`${item.key}-original`,trade_date:runtime.trade_date || "2026-10-23",source_version:runtime.source_version ?? null,content_version:"a".repeat(64),strategy_version:runtime.strategy_version ?? null,state:runtime.state};
    const detail:SavedMethodRunDetail={...runtime,...identity,items:(runtime.items ?? []).map((row,index)=>({...row,item_id:`${row.state}:${index}`})),coverage:{limit:200,offset:0,total:runtime.items?.length ?? 0,returned:runtime.items?.length ?? 0,next_offset:null,possibly_truncated:false,history_semantics:"原保存范围"}};
    return [item.key,{identity,detail}] as const;
  }));
  runsMock.mockImplementation(async({method}:{method:string})=>({method,items:saved.has(method)?[saved.get(method)!.identity]:[],coverage:{limit:30,possibly_truncated:false,history_semantics:"原保存范围"}}));
  runMock.mockImplementation(async(identity:SavedMethodRunIdentity)=>saved.get(identity.method)!.detail);
}

describe("StrategyHealthTab", () => {
  it("把「判不出」标注出来，不与健康混同", async () => {
    stub(
      {
        strategies: [{ strategy_key: "daily_picks", name: "每日精选", status: "insufficient",
                       basis: "market_neutral", lifecycle: "active" }],
        counts: { total: 1, evaluable: 0, attention: 0 },
        caveat: "insufficient/thin/no_pipeline 均为「判不出」，不是 ok",
    });
    render(<StrategyHealthTab />);
    await waitFor(() => expect(screen.getByText(/每日精选/)).toBeTruthy());
    // 必须出现"判不出"字样——否则用户会以为一切正常
    // 精确断言徽标本身：全文匹配会被 caveat 里的"判不出"掩盖（注入验证抓到过）
    expect(screen.getByTestId("status-badge-insufficient").textContent).toContain("判不出");
    expect(screen.queryByText("健康")).toBeNull();
  });

  it("有判定的状态显示对应徽标（不带判不出字样）", async () => {
    stub({
        strategies: [{ strategy_key: "k", name: "某策略", status: "ok", basis: "absolute" }],
        counts: { total: 1, evaluable: 1, attention: 0 },
      }, []);
    render(<StrategyHealthTab />);
    await waitFor(() => expect(screen.getByText(/某策略/)).toBeTruthy());
    const badge = screen.getByTestId("status-badge-ok");
    expect(badge.textContent).toContain("健康");
    expect(badge.textContent).not.toContain("判不出");
  });

  it("显示 basis 口径（两种口径不可互相解释）", async () => {
    stub({ strategies: [{ strategy_key: "k", name: "某策略", status: "ok",
                               basis: "market_neutral" }] }, []);
    render(<StrategyHealthTab />);
    await waitFor(() => expect(screen.getByText(/market_neutral/)).toBeTruthy());
  });

  it("核验结论缺失时如实说明，不留白", async () => {
    stub({ strategies: [{ strategy_key: "k", name: "某策略", status: "ok" }] }, [{ key: "k", verification: { available: false, reason: "尚无核验产物" } }]);
    render(<StrategyHealthTab />);
    await waitFor(() => expect(screen.getByText(/尚无产物/)).toBeTruthy());
  });

  it("核验结论存在时展示 verdict 与超期标记", async () => {
    stub({ strategies: [{ strategy_key: "k", name: "某策略", status: "ok" }] }, [{ key: "k", verification: { available: true, stale: true, verdict: "reject",
                                             headline: "五步全通过 ⇒ reject" } }]);
    render(<StrategyHealthTab />);
    // headline 里同样含 reject ⇒ 用 getAll
    await waitFor(() => expect(screen.getAllByText(/reject/).length).toBeGreaterThan(0));
    expect(screen.getByText(/产物超期/)).toBeTruthy();
  });

  it("接口失败时给出可见错误，不静默空白", async () => {
    healthMock.mockRejectedValue(new Error("网络异常"));
    registryMock.mockResolvedValue([]);
    render(<StrategyHealthTab />);
    await waitFor(() => expect(screen.getByText(/加载失败/)).toBeTruthy());
  });
});

describe("方法目录 · 保存运行与联动", () => {
  const daily = {key:"daily_picks", name:"每日精选六维组合", status:"active", basis:"market_neutral",
    note:"原六维规则与条件", runtime:{state:"saved",trade_date:"2026-10-23",run_at:"2026-10-23T09:26:00+08:00",
      source_version:"original-run-v1",strategy_version:"method-v1",elapsed_ms:null,
      counts:{candidates:40,scored:24,selected:1,rejected_saved:1},criteria:{min_pick_score:50,max_picks:3},
      items:[{symbol:"000538",name:"云南白药",state:"selected",score:0,reason:"原零分仍完整保留",basis:{tech:"原技术依据"}},
        {symbol:"000333",name:"美的集团",state:"rejected",score:null,reason:"源时间未核，不执行"}]}};
  const rejected = {key:"triple_volume",name:"三倍量战法",status:"rejected",note:"原否决依据，不能换名启用"};
  const observing = {key:"pullback_reversal",name:"超跌反攻",status:"observing",note:"观察不接入"};

  it("登记结果在健康失败时仍完整可读，并按生命周期筛选", async () => {
    healthMock.mockRejectedValue(new Error("health unavailable"));
    registryMock.mockResolvedValue([daily,rejected,observing]);
    render(<StrategyHealthTab/>);
    await screen.findByText("每日精选六维组合");
    expect(screen.getByText("三倍量战法")).toBeTruthy();
    expect(screen.getByText("超跌反攻")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"已否决"}));
    expect(screen.queryByText("每日精选六维组合")).toBeNull();
    expect(screen.getByText("三倍量战法")).toBeTruthy();
    expect(screen.getByText(/原否决依据，不能换名启用/)).toBeTruthy();
  });

  it("本地搜索对中文输入保持完整词边界，不新增请求", async () => {
    stub({strategies:[]},[daily,rejected,observing]);
    render(<StrategyHealthTab/>);
    await screen.findByText("每日精选六维组合");
    const search = screen.getByRole("textbox",{name:"搜索方法"});
    fireEvent.compositionStart(search);
    fireEvent.change(search,{target:{value:"超跌"}});
    expect(screen.getByText("每日精选六维组合")).toBeTruthy();
    fireEvent.compositionEnd(search,{data:"超跌"});
    expect(screen.queryByText("每日精选六维组合")).toBeNull();
    expect(screen.getByText("超跌反攻")).toBeTruthy();
    expect(registryMock).toHaveBeenCalledTimes(1);
    expect(healthMock).toHaveBeenCalledTimes(1);
  });

  it("真实0分、未知分数、耗时与分母保持原量纲，按需读拒因", async () => {
    stub({strategies:[]},[daily]);
    render(<StrategyHealthTab/>);
    expect(await screen.findByText("原分 0")).toBeTruthy();
    expect(screen.getByText(/耗时未记录/)).toBeTruthy();
    expect(screen.getByText("候选母集").nextElementSibling?.textContent).toBe("40");
    expect(screen.getByText("评分成功").nextElementSibling?.textContent).toBe("24");
    expect(screen.queryByText("美的集团")).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"拒绝存档"}));
    expect(screen.getByText("美的集团")).toBeTruthy();
    expect(screen.getByText("未保存分数")).toBeTruthy();
    expect(screen.getByText("源时间未核，不执行")).toBeTruthy();
    expect(screen.queryByText("云南白药")).toBeNull();
    expect(registryMock).toHaveBeenCalledTimes(1);
  });

  it("原入选链接到当前名单/独立执行/同日报告，不伪造历史执行", async () => {
    stub({strategies:[]},[daily]);
    render(<StrategyHealthTab/>);
    await screen.findByText("云南白药");
    expect(screen.getByRole("link",{name:"另看当前名单（非本次历史）"}).getAttribute("href")).toBe("/hunting?view=discover&sec=daily");
    expect(screen.queryByRole("link",{name:"执行与成交核对"})).toBeNull();
    expect(screen.getByRole("link",{name:"同日复盘"}).getAttribute("href")).toBe("/agent?area=research&tab=review&date=2026-10-23");
    expect(screen.getByRole("link",{name:"云南白药"}).getAttribute("href")).toContain("symbol=000538");
  });

  it("盘中阶段行不冒独立股票分母，截断保留未知", async () => {
    stub({strategies:[]},[{key:"intraday_watch",name:"盘中跟踪台账",status:"active",runtime:{state:"saved",
      trade_date:"2026-10-23",source_version:"intraday-run",possibly_truncated:true,
      counts:{candidate_rows:80,rank_rows:40,hard_gate_rows:11,selected:null},items:[]}}]);
    render(<StrategyHealthTab/>);
    await screen.findByText(/各阶段按行计数/);
    expect(screen.getByText(/各阶段按行计数/)).toBeTruthy();
    expect(screen.getByText(/原入选总数未知/)).toBeTruthy();
    expect(screen.getAllByText("原入选").some(el=>el.nextElementSibling?.textContent==="未记录")).toBe(true);
    expect(screen.getByRole("link",{name:"另看当前名单（非本次历史）"}).getAttribute("href")).toBe("/hunting?view=discover&sec=candidates");
  });

  it("空目录搜索提供恢复路径，不把运行缺失称未执行", async () => {
    stub({strategies:[]},[observing]);
    render(<StrategyHealthTab/>);
    await screen.findByText("超跌反攻");
    expect(screen.getByText(/尚无本版可读取的保存运行/)).toBeTruthy();
    fireEvent.change(screen.getByRole("textbox",{name:"搜索方法"}),{target:{value:"不存在的方法"}});
    expect(screen.getByText(/清空搜索或切换生命周期/)).toBeTruthy();
    expect(screen.queryByText("另看当前名单（非本次历史）")).toBeNull();
  });
});

it("方法结果下拉继续加载保全所有已读行，不重复请求运行", async () => {
  const rect = vi.spyOn(HTMLElement.prototype,"getBoundingClientRect").mockReturnValue({
    x:0,y:4000,top:4000,bottom:4001,left:0,right:1,width:1,height:1,toJSON:()=>({}),
  });
  try {
    stub({strategies:[]},[{key:"daily_picks",name:"有限已读运行",status:"active",runtime:{state:"saved",
      source_version:"same-run",counts:{selected:21},items:Array.from({length:21},(_,i)=>({
        symbol:String(100000+i),name:`保存个股${i+1}`,state:"selected",score:i,reason:"原入选依据"}))}}]);
    render(<StrategyHealthTab/>);
    await screen.findByText("保存个股1");
    expect(screen.getByText("保存个股20")).toBeTruthy();
    expect(screen.queryByText("保存个股21")).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"继续加载"}));
    expect(screen.getByText("保存个股21")).toBeTruthy();
    expect(screen.getByText(/已全部加载（21/)).toBeTruthy();
    expect(registryMock).toHaveBeenCalledTimes(1);
  } finally {rect.mockRestore();}
});

it("硬门通过、待核、拒绝按原 decision 展示，不互相冒充", async () => {
  stub({strategies:[]},[{key:"intraday_watch",name:"原硬门状态",runtime:{state:"saved",items:[
    {symbol:"600000",name:"通过股",state:"candidate",stage:"hard_gate",decision:"passed",score:null},
    {symbol:"600001",name:"待核股",state:"unknown",stage:"hard_gate",decision:"unknown",score:null},
    {symbol:"600002",name:"阻断股",state:"rejected",stage:"hard_gate",decision:"blocked",score:null},
    {symbol:"600003",name:"拒绝股",state:"rejected",stage:"hard_gate",decision:"rejected",score:null},
  ]}}]);
  render(<StrategyHealthTab/>);
  await screen.findByText(/各阶段按行计数/);
  fireEvent.click(screen.getByRole("button",{name:"候选"}));
  expect(screen.getByText("通过股")).toBeTruthy();
  expect(screen.getByText("硬门：通过")).toBeTruthy();
  expect(screen.queryByText("待核股")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"待核"}));
  expect(screen.getByText("待核股")).toBeTruthy();
  expect(screen.getByText("硬门：待核")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"拒绝存档"}));
  expect(screen.getByText("硬门：阻断")).toBeTruthy();
  expect(screen.getByText("硬门：未过")).toBeTruthy();
});

it("损坏的原名单明确提示并保留未知计数，不制造零入选", async () => {
  stub({strategies:[]},[{key:"daily_picks",name:"部分存档",runtime:{state:"partial",reason:"入选名单 JSON 损坏",
    counts:{selected:null,rejected_saved:1},items:[{symbol:"600001",name:"可读拒绝股",state:"rejected",score:0}]}}]);
  render(<StrategyHealthTab/>);
  await screen.findByText(/部分存档不可读取/);
  expect(screen.getByRole("status").textContent).toContain("入选名单 JSON 损坏");
  expect(screen.getAllByText("原入选").some(el=>el.nextElementSibling?.textContent==="未记录")).toBe(true);
  fireEvent.click(screen.getByRole("button",{name:"拒绝存档"}));
  expect(screen.getByText("可读拒绝股")).toBeTruthy();
  expect(screen.getByText("原分 0")).toBeTruthy();
});

it("已保存评分依据不误称没有依据，拒绝理由与原分分开", async () => {
  stub({strategies:[]},[{key:"daily_picks",name:"真实理由投影",runtime:{state:"saved",items:[
    {symbol:"600000",name:"有依据股",state:"selected",score:70,basis:{tech:"原技术依据"}},
    {symbol:"600001",name:"低分股",state:"rejected",score:47.2,reason:"低于原入选分线；原记录差额 2.8 分"},
  ]}}]);
  render(<StrategyHealthTab/>);
  await screen.findByText("有依据股");
  expect(screen.getByText(/原评分依据已保存，可展开核对/)).toBeTruthy();
  expect(screen.queryByText(/原存档没有简短理由/)).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"核对有依据股原评分与缺项"}));
  fireEvent.click(screen.getByRole("button",{name:"原合成贡献与依据"}));
  expect(screen.getByText("原技术依据")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"拒绝存档"}));
  expect(screen.getByText("原分 47.2")).toBeTruthy();
  expect(screen.getByText("低于原入选分线；原记录差额 2.8 分")).toBeTruthy();
  expect(screen.queryByText("61.1")).toBeNull();
});

it("精确历史链接不被登记册最近运行覆盖", async () => {
  const historical:SavedMethodRunIdentity={method:"daily_picks",run_id:"saved-20261022",trade_date:"2026-10-22",source_version:"original-22",content_version:"b".repeat(64),strategy_version:"method-22",state:"saved"};
  window.history.replaceState({},"",`/agent?area=research&tab=strategies&method=daily_picks&date=2026-10-22&run_id=${historical.run_id}&source_version=${historical.source_version}&strategy_version=method-22&content_version=${historical.content_version}`);
  stub({strategies:[]},[{key:"daily_picks",name:"历史按批读取",runtime:{state:"saved",trade_date:"2026-10-23",source_version:"latest-23",items:[{symbol:"600001",name:"最近运行个股",state:"selected",score:80}]}}]);
  runsMock.mockResolvedValue({method:"daily_picks",items:[],coverage:{limit:30,possibly_truncated:false,history_semantics:"每日仅保留本日当前存档"}});
  runMock.mockResolvedValue({...historical,items:[{item_id:"selected:0",symbol:"600000",name:"链接绑定历史个股",state:"selected",score:0}],coverage:{limit:200,offset:0,total:1,returned:1,next_offset:null,possibly_truncated:false,history_semantics:"原保存范围"}});
  render(<StrategyHealthTab/>);
  expect(await screen.findByText("链接绑定历史个股")).toBeTruthy();
  expect(screen.queryByText("最近运行个股")).toBeNull();
  expect(runMock.mock.calls[0][0]).toMatchObject(historical);
  expect(screen.getByRole("link",{name:"另看当前名单（非本次历史）"})).toBeTruthy();
});
