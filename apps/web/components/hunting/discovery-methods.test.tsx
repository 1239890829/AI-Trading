import {useState} from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { StrategyRegistryItem } from "@/lib/api/picks";

const {registryMock, runsMock, healthMock} = vi.hoisted(() => ({
  registryMock: vi.fn(), runsMock: vi.fn(), healthMock: vi.fn(),
}));
vi.mock("@/lib/api/picks", async () => ({...await vi.importActual<typeof import("@/lib/api/picks")>("@/lib/api/picks"),
  getStrategyRegistry: (options: {signal?: AbortSignal; includeCandidates?: boolean}) => registryMock(options), getStrategyMethodRuns: () => runsMock(),
  getStrategyHealth: () => healthMock(),
}));

import { DiscoveryMethods, discoveryMethodHref, discoveryMethodScope, methodSupportsManualResearch, validMethodRow } from "./discovery-methods";
import {DiscoveryMethodDetail} from "./discovery-method-detail";

const favoritesKey="ashare.discovery.methods.favorites.v1";
function referenceMethod(index=0,asset:"stock"|"etf"="stock"): StrategyRegistryItem {
  return {key:`reference:method_${index}`,catalog_kind:"reference",name:`来源方法${index}`,status:asset==="etf"?"out_of_scope":"candidate",
    candidate:{source_label:"TSP来源目录",source_code:`method_${index}`,source_version:"20261025:v4",asset,
      criteria:["原日收盘价站上20日线","原成交量大于前20日均量"],existing_coverage:["已有日线量价读取"],limitations:["尚未验证样本外增量"],runnable:false},
    discovery:{category:"trend",category_label:"趋势量价",session:"after_hours",session_label:"盘后参考",summary:"原量价规则，仅供评价",supports_saved_runs:false}};
}

function registeredMethods(): StrategyRegistryItem[] {
  return [
    {key:"daily_picks", name:"每日精选六维组合", status:"active", note:"未证实的99.9%不应出现在此处",
      discovery:{category:"multi_factor",category_label:"多因子组合",session:"daily",session_label:"每日生成",summary:"六维评分形成已存名单；入选与模拟执行分别核验",supports_saved_runs:true},
      runtime:{state:"saved",trade_date:"2026-10-23",source_version:"original-selection",strategy_version:"daily-v4",counts:{selected:0}}},
    {key:"intraday_watch", name:"盘中跟踪台账", status:"active",
      discovery:{category:"intraday",category_label:"盘中跟踪",session:"intraday",session_label:"盘中观察",summary:"盘中首见记录与收盘观察",supports_saved_runs:true},
      runtime:{state:"partial",trade_date:"2026-10-22",counts:{selected:null}}},
    {key:"pullback_reversal", name:"超跌反攻", status:"observing",
      discovery:{category:"reversal",category_label:"超跌反转",session:"research",session_label:"离线研究",summary:"样本外观察，尚未接入",supports_saved_runs:false},verification:{available:true,stale:true}},
    {key:"triple_volume", name:"三倍量战法", status:"rejected",
      discovery:{category:"volume",category_label:"量价形态",session:"research",session_label:"离线研究",summary:"历史验证已否决，保留反例",supports_saved_runs:false},verification:{available:false}},
    {key:"two_thirty_five", name:"两点半五步法", status:"rejected",
      discovery:{category:"timing",category_label:"盘尾时段",session:"research",session_label:"离线研究",summary:"盘尾五步方法已否决，保留反例",supports_saved_runs:false}},
  ];
}

beforeEach(() => {
  window.localStorage.clear();
  window.dispatchEvent(new StorageEvent("storage",{key:favoritesKey}));
  registryMock.mockReset().mockResolvedValue(registeredMethods());
  window.history.replaceState({},"","/hunting?view=discover");
});
afterEach(() => {cleanup(); vi.restoreAllMocks(); vi.clearAllMocks();});

describe("选股发现：真实战法分类", () => {
  it("only the three bound manual research methods become runnable without becoming production selection",async()=>{
    const manual:StrategyRegistryItem={...referenceMethod(),key:"reference:macd_golden_cross",name:"MACD金叉",status:"research_ready",candidate:{...referenceMethod().candidate!,source_code:"macd_golden_cross",runnable:true,execution:{mode:"manual_research",version:"ashare-macd-12-26-9-v1",definition_digest:"a".repeat(64),production_enabled:false,definition:"前日DIF≤DEA且当日DIF>DEA",definition_supplement:"至少60根，首个输入初始化EMA"}},discovery:{category:"trend",category_label:"趋势量价",session:"after_hours",session_label:"盘后参考",summary:"手动价格条件研究",supports_saved_runs:true}};
    expect(validMethodRow(manual)).toBe(true);expect(methodSupportsManualResearch(manual)).toBe(true);
    expect(validMethodRow({...manual,key:"reference:method_0",candidate:{...manual.candidate!,source_code:"method_0"}})).toBe(false);
    expect(validMethodRow({...manual,candidate:{...manual.candidate!,execution:{...manual.candidate!.execution!,production_enabled:true as never}}})).toBe(false);
    expect(validMethodRow({...manual,candidate:{...manual.candidate!,execution:undefined}})).toBe(false);
    registryMock.mockResolvedValue([...registeredMethods(),manual]);
    render(<DiscoveryMethods/>);
    await screen.findByText("MACD金叉");
    expect(screen.getByText("研究可运行 · 仅手动研究")).toBeTruthy();
    const row=screen.getByText("MACD金叉").closest("li")!;
    expect(within(row).getByText("保存研究结果按需读取")).toBeTruthy();
    expect(within(row).queryByText("尚无保存运行")).toBeNull();
    expect(screen.getByRole("option",{name:"现役 2"})).toBeTruthy();
    expect(runsMock).not.toHaveBeenCalled();expect(healthMock).not.toHaveBeenCalled();
  });

  it("只在激活时读一次完整目录，默认全部，不运行方法或取逐方法历史", async () => {
    const page = render(<DiscoveryMethods active={false}/>);
    expect(registryMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("region",{name:"战法分类"})).toBeNull();
    page.rerender(<DiscoveryMethods active/>);
    expect(await screen.findByText("每日精选六维组合")).toBeTruthy();
    const list = screen.getByRole("list",{name:"已登记方法"});
    expect(within(list).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByText("三倍量战法")).toBeTruthy();
    expect(screen.queryByText(/99\.9/)).toBeNull();
    expect(screen.getByRole("option",{name:"观察 1"})).toBeTruthy();
    expect(screen.getByRole("option",{name:"已否决 2"})).toBeTruthy();
    expect(registryMock).toHaveBeenCalledTimes(1);
    expect(registryMock.mock.calls[0][0].signal).toBeInstanceOf(AbortSignal);
    expect(registryMock.mock.calls[0][0].includeCandidates).toBe(true);
    expect(runsMock).not.toHaveBeenCalled();
    expect(healthMock).not.toHaveBeenCalled();
  });

  it("分类与时段采用登记字段，观察/否决有明确核验入口", async () => {
    render(<DiscoveryMethods/>);
    await screen.findByText("每日精选六维组合");
    fireEvent.change(screen.getByRole("combobox",{name:"方法状态"}),{target:{value:"all"}});
    fireEvent.change(screen.getByRole("combobox",{name:"方法类别"}),{target:{value:"volume"}});
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(1);
    expect(screen.getByRole("link",{name:"查看核验与反例：三倍量战法"})).toBeTruthy();
    expect(screen.getByText("尚无核验产物")).toBeTruthy();
    expect(screen.queryByText("每日精选六维组合")).toBeNull();
    fireEvent.change(screen.getByRole("combobox",{name:"方法时段"}),{target:{value:"daily"}});
    expect(screen.getByText("没有匹配方法。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"清除筛选"}));
    expect(within(screen.getByRole("list")).getAllByRole("listitem")).toHaveLength(5);
    expect(screen.getByText("核验状态未记录")).toBeTruthy();
    fireEvent.change(screen.getByRole("combobox",{name:"方法状态"}),{target:{value:"observing"}});
    expect(screen.getByText("有核验产物 · 需复核")).toBeTruthy();
    expect(screen.queryByRole("link",{name:/查看原运行/})).toBeNull();
  });

  it("中文组合输入完成后才执行搜索，不以长note代替简短用途", async () => {
    render(<DiscoveryMethods/>);
    await screen.findByText("每日精选六维组合");
    const input = screen.getByRole("textbox",{name:"搜索战法"});
    fireEvent.compositionStart(input);
    fireEvent.change(input,{target:{value:"超跌"}});
    expect(screen.getByText("每日精选六维组合")).toBeTruthy();
    fireEvent.compositionEnd(input,{data:"超跌"});
    expect(screen.getByText("超跌反攻")).toBeTruthy();
    expect(screen.queryByText("每日精选六维组合")).toBeNull();
    fireEvent.change(screen.getByRole("combobox",{name:"方法状态"}),{target:{value:"active"}});
    expect(screen.getByText("没有匹配方法。")).toBeTruthy();
    expect(registryMock).toHaveBeenCalledTimes(1);
  });

  it("0入选与缺少计数分开；最近保存不冒充所选日期的结果", async () => {
    render(<DiscoveryMethods date="2026-10-23" search="date=2026-10-23&account=paper"/>);
    await screen.findByText("每日精选六维组合");
    const rows = within(screen.getByRole("list")).getAllByRole("listitem");
    expect(rows[0].textContent).toContain("入选 0 只");
    expect(rows[0].textContent).toContain("方法版本 daily-v4");
    expect(rows[1].textContent).toContain("最近保存（非所选日期） 2026-10-22");
    expect(rows[1].textContent).toContain("部分记录 · 入选数未记录");
    const target = new URL(within(rows[1]).getByRole("link",{name:/查看原运行与版本/}).getAttribute("href")!,"http://localhost");
    expect(target.searchParams.get("date")).toBe("2026-10-23");
    expect(target.searchParams.has("content_version")).toBe(false);
    expect(target.searchParams.has("run_id")).toBe(false);
    expect(target.searchParams.get("account")).toBe("paper");
  });

  it("旧服务缺元数据时不从名字或note猜类别、时段与执行能力", async () => {
    registryMock.mockResolvedValue([{key:"daily_picks",name:"盘中动量假名字",status:"active",note:"资金热度；99%胜率",runtime:{state:"saved",counts:{selected:2}}}]);
    render(<DiscoveryMethods/>);
    await screen.findByText("盘中动量假名字");
    const row = screen.getByRole("listitem");
    expect(row.textContent).toContain("未分类 · 时段未登记");
    expect(row.textContent).toContain("用途未登记");
    expect(row.textContent).not.toContain("入选 2");
    expect(screen.queryByText(/99%/)).toBeNull();
    expect(within(row).getByRole("link",{name:/查看核验与反例/})).toBeTruthy();
  });

  it("读取失败与空登记分开，刷新失败保留结果并可重试恢复", async () => {
    registryMock.mockRejectedValueOnce(new Error("offline")).mockResolvedValueOnce(registeredMethods())
      .mockRejectedValueOnce(new Error("again")).mockResolvedValue([]);
    render(<DiscoveryMethods/>);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByText("暂无已登记方法。")).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"刷新方法"}));
    await screen.findByText("每日精选六维组合");
    fireEvent.click(screen.getByRole("button",{name:"刷新方法"}));
    expect(await screen.findByText(/保留上次成功读取的登记/)).toBeTruthy();
    expect(screen.getByText("每日精选六维组合")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"刷新方法"}));
    expect(await screen.findByText("暂无已登记方法。")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("重新激活的新结果不被旧读回包覆盖；列表不另建滚动容器", async () => {
    let finishOld!: (rows: StrategyRegistryItem[]) => void;
    registryMock.mockReturnValueOnce(new Promise(resolve => {finishOld = resolve;})).mockResolvedValueOnce(registeredMethods());
    const page = render(<DiscoveryMethods/>);
    await waitFor(() => expect(registryMock).toHaveBeenCalledTimes(1));
    const oldSignal = registryMock.mock.calls[0][0].signal as AbortSignal;
    expect(oldSignal.aborted).toBe(false);
    page.rerender(<DiscoveryMethods active={false}/>);
    expect(oldSignal.aborted).toBe(true);
    page.rerender(<DiscoveryMethods active/>);
    await screen.findByText("每日精选六维组合");
    await act(async () => finishOld([{key:"old",name:"过时方法",status:"active"}]));
    expect(screen.queryByText("过时方法")).toBeNull();
    expect(page.container.querySelector('[class*="overflow-y"]')).toBeNull();
    expect(registryMock).toHaveBeenCalledTimes(2);
  });
});

describe("完整来源目录的选择与本机关注",()=>{
  it("默认显示5登记加35来源候选，可筛31个A股候选与4个ETF范围未开放",async()=>{
    registryMock.mockResolvedValue([...registeredMethods(),...Array.from({length:35},(_,index)=>referenceMethod(index,index<31?"stock":"etf"))]);
    render(<DiscoveryMethods/>);
    await screen.findByText("来源方法34");
    expect(within(screen.getByRole("list",{name:"已登记方法"})).getAllByRole("listitem")).toHaveLength(40);
    expect(screen.getByRole("option",{name:"来源候选 31"})).toBeTruthy();
    expect(screen.getByRole("option",{name:"范围未开放 4"})).toBeTruthy();
    fireEvent.change(screen.getByRole("combobox",{name:"方法资产"}),{target:{value:"etf"}});
    expect(screen.getAllByRole("listitem")).toHaveLength(4);
    expect(screen.getAllByText("范围未开放 · ETF参考")).toHaveLength(4);
    expect(screen.queryByRole("link",{name:/查看原运行|查看核验/})).toBeNull();
    fireEvent.change(screen.getByRole("combobox",{name:"方法资产"}),{target:{value:"stock"}});
    fireEvent.change(screen.getByRole("combobox",{name:"方法状态"}),{target:{value:"candidate"}});
    expect(screen.getAllByRole("listitem")).toHaveLength(31);
    expect(registryMock).toHaveBeenCalledTimes(1);expect(runsMock).not.toHaveBeenCalled();expect(healthMock).not.toHaveBeenCalled();
    expect(screen.queryByText(/胜率|均收益/)).toBeNull();
  });

  it("按disc_method精确选择同层规则，保留原日期和旧运行身份但不消费旧运行",async()=>{
    const candidate=referenceMethod();registryMock.mockResolvedValue([...registeredMethods(),candidate]);
    const target=vi.fn(),navigate=vi.fn();
    function Harness() {
      const [search,setSearch]=useState("browse=methods&date=2026-10-22&sec=daily&selection=600519&method=daily_picks&run_id=old");
      return <DiscoveryMethods search={search} date="2026-10-22" onMethodTarget={target} onNavigate={(href,push)=>{navigate(href,push);setSearch(new URL(href,"http://localhost").search);}}/>;
    }
    render(<Harness/>);
    const link=await screen.findByRole("link",{name:"查看方法规则：来源方法0"});
    link.focus();expect(document.activeElement).toBe(link);
    fireEvent.click(link);
    expect(target.mock.lastCall).toEqual([discoveryMethodScope("2026-10-22",candidate.key),candidate]);
    const url=new URL(navigate.mock.lastCall![0],"http://localhost");
    expect(url.searchParams.get("disc_method")).toBe(candidate.key);expect(url.searchParams.get("date")).toBe("2026-10-22");
    expect(url.searchParams.get("sec")).toBe("daily");expect(url.searchParams.get("selection")).toBe("600519");
    expect(url.searchParams.get("method")).toBe("daily_picks");expect(url.searchParams.get("run_id")).toBe("old");
    expect(navigate.mock.lastCall![1]).toBe(true);expect(link.getAttribute("aria-current")).toBe("true");
    expect(registryMock).toHaveBeenCalledTimes(1);expect(runsMock).not.toHaveBeenCalled();
  });

  it("未知旧链接不退第一方法，日期scope变化和关闭会清退旧target，不重取目录",async()=>{
    const target=vi.fn();
    const view=render(<DiscoveryMethods search="browse=methods&disc_method=missing" date="2026-10-22" onMethodTarget={target}/>);
    expect(await screen.findByText("链接指定的方法不在本次目录，未换成其他方法。")).toBeTruthy();
    expect(target.mock.lastCall).toEqual([discoveryMethodScope("2026-10-22","missing"),null]);
    view.rerender(<DiscoveryMethods search="browse=methods&disc_method=daily_picks" date="2026-10-23" onMethodTarget={target}/>);
    expect(target.mock.lastCall?.[0]).toBe(discoveryMethodScope("2026-10-23","daily_picks"));
    expect(target.mock.lastCall?.[1]?.key).toBe("daily_picks");
    view.rerender(<DiscoveryMethods active={false} search="browse=methods&disc_method=daily_picks" date="2026-10-23" onMethodTarget={target}/>);
    expect(target.mock.lastCall).toEqual([discoveryMethodScope("2026-10-23","daily_picks"),null]);
    expect(registryMock).toHaveBeenCalledTimes(1);
  });

  it("关注和右栏共享本机偏好、可筛我关注，重新挂载仍保留，不改变候选状态",async()=>{
    const candidate=referenceMethod();registryMock.mockResolvedValue([candidate]);
    const view=render(<><DiscoveryMethods/><DiscoveryMethodDetail row={candidate} onClose={vi.fn()}/></>);
    const list=await screen.findByRole("list",{name:"已登记方法"});
    fireEvent.click(within(list).getByRole("button",{name:"关注来源方法0"}));
    expect(screen.getAllByRole("button",{name:"取消关注来源方法0"})).toHaveLength(2);
    expect(JSON.parse(window.localStorage.getItem(favoritesKey)!)).toEqual([candidate.key]);
    fireEvent.click(screen.getByRole("button",{name:"我关注 1"}));
    expect(within(list).getAllByRole("listitem")).toHaveLength(1);
    expect(candidate.status).toBe("candidate");expect(candidate.candidate?.runnable).toBe(false);
    view.unmount();render(<DiscoveryMethods/>);
    expect(await screen.findByRole("button",{name:"取消关注来源方法0"})).toBeTruthy();
    expect(runsMock).not.toHaveBeenCalled();expect(healthMock).not.toHaveBeenCalled();
  });

  it("本机关注上限40，畸形偏好不当成目录或执行权限",async()=>{
    window.localStorage.setItem(favoritesKey,JSON.stringify(Array.from({length:45},(_,index)=>`reference:old_${index}`)));
    window.dispatchEvent(new StorageEvent("storage",{key:favoritesKey}));
    registryMock.mockResolvedValue([referenceMethod()]);render(<DiscoveryMethods/>);
    fireEvent.click(await screen.findByRole("button",{name:"关注来源方法0"}));
    expect(screen.getByText("本机最多关注40个方法，请先取消一项。")).toBeTruthy();
    expect(screen.getByRole("button",{name:"我关注 40"})).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"我关注 40"}));
    fireEvent.click(screen.getByRole("button",{name:"清空本机关注"}));
    expect(screen.getByRole("button",{name:"我关注 0"})).toBeTruthy();
    expect(JSON.parse(window.localStorage.getItem(favoritesKey)!)).toEqual([]);
    expect(screen.queryByText("本机最多关注40个方法，请先取消一项。")).toBeNull();
    expect(runsMock).not.toHaveBeenCalled();
  });

  it("拒绝重复key、候选伪装可运行及错来源身份，不把异常行送到右栏",async()=>{
    const candidate=referenceMethod();
    registryMock.mockResolvedValue([candidate,candidate,{...referenceMethod(1),candidate:{...referenceMethod(1).candidate,runnable:true}},
      {...referenceMethod(2),key:"reference:wrong_source"},{...referenceMethod(3),name:{unexpected:"name"}}]);
    const target=vi.fn();render(<DiscoveryMethods search={`disc_method=${candidate.key}`} onMethodTarget={target}/>);
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.queryByRole("link",{name:/查看方法规则/})).toBeNull();
    expect(target.mock.lastCall).toEqual([discoveryMethodScope(undefined,candidate.key),null]);
  });

  it("来源详情逐项给出规则、覆盖与限制，ETF不开放，不创建伪运行或假效果",()=>{
    const candidate=referenceMethod(31,"etf"),close=vi.fn();
    const view=render(<DiscoveryMethodDetail row={candidate} date="2026-10-22" search="method=daily_picks&run_id=old" onClose={close}/>);
    expect(screen.getByRole("heading",{name:"来源方法31"}).tabIndex).toBe(-1);
    expect(screen.getByText("来源候选，不可运行；关注不会启用方法或生成选股。")).toBeTruthy();
    expect(screen.getByRole("region",{name:"原规则"}).textContent).toContain("原日收盘价站上20日线");
    expect(screen.getByRole("region",{name:"现有覆盖"}).textContent).toContain("已有日线量价读取");
    expect(screen.getByRole("region",{name:"仍缺什么"}).textContent).toContain("尚未验证样本外增量");
    expect(screen.getByText(/TSP来源目录 · 原版本 20261025:v4 · method_31/)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();expect(screen.queryByText(/胜率|均收益/)).toBeNull();
    expect(view.container.querySelector('[class*="overflow"]')).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"返回方法目录"}));expect(close).toHaveBeenCalledTimes(1);
    expect(registryMock).not.toHaveBeenCalled();expect(runsMock).not.toHaveBeenCalled();
  });

  it("现役右栏仍链接准确原运行，不展示来源候选为现役",()=>{
    const row=registeredMethods()[0];
    render(<DiscoveryMethodDetail row={row} date="2026-10-22" search={`method=daily_picks&date=2026-10-23&run_id=old&content_version=${"a".repeat(64)}&source_version=old&disc_method=daily_picks`} onClose={vi.fn()}/>);
    const url=new URL(screen.getByRole("link",{name:"查看原运行与版本"}).getAttribute("href")!,"http://localhost");
    expect(url.searchParams.get("method")).toBe("daily_picks");expect(url.searchParams.get("date")).toBe("2026-10-22");
    expect(url.searchParams.has("run_id")).toBe(false);expect(url.searchParams.has("disc_method")).toBe(false);
    expect(screen.getByText(/最近保存（非所选日期） 2026-10-23/)).toBeTruthy();
    expect(registryMock).not.toHaveBeenCalled();expect(runsMock).not.toHaveBeenCalled();
  });

  it("存储拒绝时目录与关注仍可用，但明确本页偏好未保存",async()=>{
    vi.spyOn(Storage.prototype,"setItem").mockImplementation(()=>{throw new Error("blocked");});
    registryMock.mockResolvedValue([referenceMethod()]);render(<DiscoveryMethods/>);
    fireEvent.click(await screen.findByRole("button",{name:"关注来源方法0"}));
    expect(screen.getByRole("button",{name:"取消关注来源方法0"})).toBeTruthy();
    expect(screen.getByText("本机存储不可用，关注仅保留在当前页面，尚未保存。")).toBeTruthy();
    expect(registryMock).toHaveBeenCalledTimes(1);expect(runsMock).not.toHaveBeenCalled();
  });

  it("畸形偏好或读取存储被拒绝均不阻断来源目录",async()=>{
    const view=render(<DiscoveryMethods/>);
    await screen.findByText("每日精选六维组合");
    act(()=>{window.localStorage.setItem(favoritesKey,"{not-json");window.dispatchEvent(new StorageEvent("storage",{key:favoritesKey}));});
    expect(screen.getByRole("button",{name:"我关注 0"})).toBeTruthy();
    view.unmount();
    vi.spyOn(Storage.prototype,"getItem").mockImplementation(()=>{throw new Error("read blocked");});
    render(<DiscoveryMethods/>);
    await screen.findByText("每日精选六维组合");
    expect(screen.getByText("本机存储不可用，关注仅保留在当前页面，尚未保存。")).toBeTruthy();
    expect(screen.getByRole("link",{name:"查看方法规则：每日精选六维组合"})).toBeTruthy();
    expect(runsMock).not.toHaveBeenCalled();
  });
});

describe("方法原版本读取链接", () => {
  const hash = "a".repeat(64);
  const original = `method=daily_picks&date=2026-10-23&run_id=original-run&content_version=${hash}&source_version=source-23&strategy_version=daily-v4&run_state=rejected&run_row=row-2&account=paper&symbol=600519&view=discover&sec=daily&browse=methods&disc_kind=concept&disc_range=intraday&disc_code=old-code&disc_q=test&disc_sort=heat&disc_method=daily_picks&selection=daily`;
  const params = (key: string, search = original, date?: string) => new URL(discoveryMethodHref(key,search,date),"http://localhost").searchParams;

  it("同方法同日期保留准确原运行、内容、来源与方法版本", () => {
    const query = params("daily_picks");
    expect(query.get("area")).toBe("research");
    expect(query.get("tab")).toBe("strategies");
    expect(query.get("content_version")).toBe(hash);
    expect(query.get("source_version")).toBe("source-23");
    expect(query.get("run_id")).toBe("original-run");
    expect(query.get("strategy_version")).toBe("daily-v4");
    expect(query.get("run_state")).toBe("rejected");
    expect(query.get("run_row")).toBe("row-2");
    expect(query.get("symbol")).toBe("600519");
    expect(query.get("account")).toBe("paper");
    expect(query.has("view")).toBe(false);
    expect(query.has("sec")).toBe(false);
    for (const key of ["browse","disc_kind","disc_range","disc_code","disc_q","disc_sort","disc_method","selection"]) expect(query.has(key)).toBe(false);
    expect(query.get("from")).toBe(`/hunting?${original}`);
  });

  it("换方法保留日期账户但清除他法版本；换日期也清旧运行，不退最新", () => {
    const other = params("intraday_watch");
    expect(other.get("date")).toBe("2026-10-23");
    expect(other.get("account")).toBe("paper");
    for (const key of ["run_id","content_version","source_version","strategy_version","run_state","run_row"]) expect(other.has(key)).toBe(false);
    const nextDay = params("daily_picks",original,"2026-10-22");
    expect(nextDay.get("date")).toBe("2026-10-22");
    for (const key of ["run_id","content_version","source_version","strategy_version","run_row"]) expect(nextDay.has(key)).toBe(false);
    expect(params("daily_picks",original,"20261023").get("run_id")).toBe("original-run");
    expect(params("daily_picks",original,"invalid-date").get("date")).toBe("invalid-date");
    const orphaned = params("daily_picks",original.replace("method=daily_picks&", ""));
    expect(orphaned.get("date")).toBe("2026-10-23");
    for (const key of ["run_id","content_version","source_version","strategy_version","run_state","run_row"]) expect(orphaned.has(key)).toBe(false);
  });
});
