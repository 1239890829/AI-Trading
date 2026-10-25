import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import type { StrategyRegistryItem } from "@/lib/api/picks";

const {registryMock, runsMock, healthMock} = vi.hoisted(() => ({
  registryMock: vi.fn(), runsMock: vi.fn(), healthMock: vi.fn(),
}));
vi.mock("@/lib/api/picks", () => ({
  getStrategyRegistry: (options: {signal?: AbortSignal}) => registryMock(options), getStrategyMethodRuns: () => runsMock(),
  getStrategyHealth: () => healthMock(),
}));

import { DiscoveryMethods, discoveryMethodHref } from "./discovery-methods";

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
  registryMock.mockResolvedValue(registeredMethods());
  window.history.replaceState({},"","/hunting?view=discover");
});
afterEach(() => {cleanup(); vi.clearAllMocks();});

describe("选股发现：真实战法分类", () => {
  it("只在激活时读一次登记册，默认现役，不运行方法或取逐方法历史", async () => {
    const page = render(<DiscoveryMethods active={false}/>);
    expect(registryMock).not.toHaveBeenCalled();
    expect(screen.queryByRole("region",{name:"战法分类"})).toBeNull();
    page.rerender(<DiscoveryMethods active/>);
    expect(await screen.findByText("每日精选六维组合")).toBeTruthy();
    const list = screen.getByRole("list",{name:"已登记方法"});
    expect(within(list).getAllByRole("listitem")).toHaveLength(2);
    expect(screen.queryByText("三倍量战法")).toBeNull();
    expect(screen.queryByText(/99\.9/)).toBeNull();
    expect(screen.getByRole("button",{name:"观察 1"})).toBeTruthy();
    expect(screen.getByRole("button",{name:"已否决 2"})).toBeTruthy();
    expect(registryMock).toHaveBeenCalledTimes(1);
    expect(registryMock.mock.calls[0][0].signal).toBeInstanceOf(AbortSignal);
    expect(runsMock).not.toHaveBeenCalled();
    expect(healthMock).not.toHaveBeenCalled();
  });

  it("分类与时段采用登记字段，观察/否决有明确核验入口", async () => {
    render(<DiscoveryMethods/>);
    await screen.findByText("每日精选六维组合");
    fireEvent.click(screen.getByRole("button",{name:"全部 5"}));
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
    fireEvent.click(screen.getByRole("button",{name:"观察 1"}));
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
    expect(screen.getByText("没有匹配方法。")).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"全部 5"}));
    expect(screen.getByText("超跌反攻")).toBeTruthy();
    expect(screen.queryByText("每日精选六维组合")).toBeNull();
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
    const target = new URL(within(rows[1]).getByRole("link").getAttribute("href")!,"http://localhost");
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

describe("方法原版本读取链接", () => {
  const hash = "a".repeat(64);
  const original = `method=daily_picks&date=2026-10-23&run_id=original-run&content_version=${hash}&source_version=source-23&strategy_version=daily-v4&run_state=rejected&run_row=row-2&account=paper&symbol=600519&view=discover&sec=daily&browse=methods&disc_kind=concept&disc_range=intraday&disc_code=old-code&disc_q=test&disc_sort=heat&selection=daily`;
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
    for (const key of ["browse","disc_kind","disc_range","disc_code","disc_q","disc_sort","selection"]) expect(query.has(key)).toBe(false);
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
