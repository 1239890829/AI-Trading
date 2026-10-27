import {useState} from "react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {act, cleanup, fireEvent, render, screen, waitFor, within} from "@testing-library/react";
import type {OfficialBoardQuotes, OfficialCatalogKind, OfficialCatalogPayload} from "@/lib/api/themes";

const {jsonMock} = vi.hoisted(() => ({jsonMock:vi.fn()}));
vi.mock("@/lib/api/internal", async () => ({...await vi.importActual<typeof import("@/lib/api/internal")>("@/lib/api/internal"), getJson:(...args: unknown[]) => jsonMock(...args)}));
import {getConceptDetail, getOfficialBoardQuotes, getStockThemes} from "@/lib/api/themes";
import {DiscoveryOfficialDirectory} from "./discovery-official-directory";

const target = vi.fn(), navigate = vi.fn();
const far = {top:5000, bottom:5001, left:0, right:100, width:100, height:1, x:0, y:5000, toJSON:() => ({})};
function catalog(kind:OfficialCatalogKind="concept", count=80): OfficialCatalogPayload {
  // Synthetic unit fixtures exercise the official-code shape, not source membership or live quotes.
  return {source:"ths_official", kind, total:count, synced_at:"2026-10-25T08:00:00+08:00",
    items:Array.from({length:count}, (_,index) => ({code:`${(kind === "concept" ? 300000 : 400000)+index}.TI`, name:`${kind === "concept" ? "正式概念" : "正式行业"}${index}`,
      source:"ths_official",kind,synced_at:"2026-10-25T08:00:00+08:00"}))};
}
function quoteReply(kind:OfficialCatalogKind, codes:string[]): OfficialBoardQuotes {
  return {available:true, kind, source:"ths_official", source_basis:"index_snapshot", source_as_of:null, source_ready_at:"2026-10-25T09:50:00+08:00",
    received_at:"2026-10-25T09:50:01+08:00", rows:codes.map(code => ({code, change_pct:0, price:1000})), missing_codes:[]};
}
function route(url:string) {
  const parsed = new URL(url,"http://localhost");
  const kind = parsed.searchParams.get("kind") === "industry" ? "industry" : "concept";
  if (parsed.pathname === "/api/themes/catalog") return {data:catalog(kind)};
  if (parsed.pathname === "/api/themes/catalog/quotes") return {data:quoteReply(kind,(parsed.searchParams.get("codes") ?? "").split(","))};
  throw new Error(`unexpected read ${parsed.pathname}`);
}
function Harness({initial="browse=catalog&disc_kind=concept", ranking=false}: {initial?:string; ranking?:boolean}) {
  const [search,setSearch] = useState(initial), params = new URLSearchParams(search);
  const kind = params.get("disc_kind") === "industry" ? "industry" : "concept";
  return <DiscoveryOfficialDirectory ranking={ranking} kind={kind} search={search} onTarget={target} onNavigate={(href,push) => {navigate(href,push);setSearch(new URL(href,"http://localhost").search);}}/>;
}
function quoteCalls() {return jsonMock.mock.calls.filter(([url]) => String(url).includes("/catalog/quotes?"));}
function quoteCodes(call:unknown[]) {return (new URL(String(call[0]),"http://localhost").searchParams.get("codes") ?? "").split(",");}
function resultRows() {return within(screen.getByRole("list",{name:"官方目录结果"})).getAllByRole("listitem");}
beforeEach(() => {
  jsonMock.mockReset().mockImplementation(async(url:string) => route(url));target.mockReset();navigate.mockReset();
  vi.spyOn(HTMLElement.prototype,"getBoundingClientRect").mockReturnValue(far);
});
afterEach(() => {cleanup();vi.restoreAllMocks();});

function rankingReply(kind:OfficialCatalogKind="concept", count=35) {
  const names=catalog(kind,count);
  return {scope:"directory",kind,source:"ths_official",source_basis:"index_snapshot",available:true,
    catalog_version:"a".repeat(64),catalog_state:"ready",catalog_synced_at:names.synced_at,
    directory_total:count,source_total:count,source_returned:count,value_count:count,
    coverage_state:"source_complete",missing_codes:[],source_as_of:null,trade_date:null,quality:"stale",observation_state:"clock_unknown",
    source_ready_from:"2026-10-25T21:19:41+08:00",source_ready_at:"2026-10-25T21:19:41+08:00",
    received_from:"2026-10-27T09:50:00+08:00",received_at:"2026-10-27T09:50:01+08:00",
    rows:names.items.map((row,index)=>({...row,price:1000,change_pct:index-10,quote_state:"value"}))};
}

describe("完整涨幅读者",()=>{
  it("完整涨幅默认降序覆盖榜尾，不用展开报价或热股样本，时点未知首屏明示",async()=>{
    jsonMock.mockResolvedValue({data:rankingReply()});
    const page=render(<DiscoveryOfficialDirectory ranking kind="concept" search="browse=hot" onTarget={target} onNavigate={navigate}/>);
    await screen.findByRole("button",{name:"查看正式概念34成分"});
    expect(resultRows()[0].textContent).toContain("正式概念34");
    expect(resultRows()).toHaveLength(24);
    expect(jsonMock.mock.calls.map(call=>call[0])).toEqual(["/api/themes/catalog/quotes?kind=concept&scope=directory"]);
    expect(screen.getByText(/来源交易日与行情时点未提供/)).toBeTruthy();
    expect(screen.getByText(/有效涨幅 35 \/ 35/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button",{name:"继续加载"}));
    expect(resultRows()).toHaveLength(35);expect(jsonMock).toHaveBeenCalledTimes(1);
    expect(page.container.querySelector('[class*="overflow"]')).toBeNull();
  });
  it("完整涨幅缺值保留原名code置后，重试实际fresh；合法零不当缺值",async()=>{
    const data=rankingReply("concept",3);
    data.rows[0].change_pct=null as unknown as number; data.rows[0].price=null as unknown as number;
    data.rows[0].quote_state="missing";data.rows[1].change_pct=0;data.rows[2].change_pct=-2;
    Object.assign(data,{source_returned:2,value_count:2,coverage_state:"partial",missing_codes:[data.rows[0].code]});
    jsonMock.mockResolvedValue({data});
    render(<DiscoveryOfficialDirectory ranking kind="concept" search="browse=hot" onTarget={target} onNavigate={navigate}/>);
    await screen.findByText(/有效涨幅 2 \/ 3/);
    expect(resultRows().map(row=>row.textContent)).toEqual([expect.stringContaining("正式概念1"),expect.stringContaining("正式概念2"),expect.stringContaining("正式概念0")]);
    expect(within(resultRows()[0]).getByText("0.00%")).toBeTruthy();
    expect(within(resultRows()[2]).queryByText("0.00%")).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"刷新涨幅"}));
    await waitFor(()=>expect(jsonMock).toHaveBeenCalledTimes(2));
    expect(String(jsonMock.mock.calls[1][0])).toContain("fresh=true");
  });
  it("完整涨幅错kind或覆盖伪全拒绝，不按部分包制造完整排名",async()=>{
    jsonMock.mockResolvedValue({data:{...rankingReply(),source_returned:34}});
    render(<DiscoveryOfficialDirectory ranking kind="concept" search="browse=hot" onTarget={target} onNavigate={navigate}/>);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent",expect.stringContaining("身份或覆盖不符"));
    expect(screen.queryByRole("button",{name:"查看正式概念34成分"})).toBeNull();
  });
  it("完整涨幅切类别取消旧包；迟到概念不得覆盖行业或重写父身份",async()=>{
    let finish!: (value:unknown)=>void;
    jsonMock.mockImplementation((url:string)=>url.includes("kind=concept")?new Promise(resolve=>{finish=resolve;}):Promise.resolve({data:rankingReply("industry",3)}));
    const page=render(<DiscoveryOfficialDirectory ranking kind="concept" search="browse=hot" scope="concept-owner" onTarget={target} onNavigate={navigate}/>);
    await waitFor(()=>expect(jsonMock).toHaveBeenCalledTimes(1));
    const signal=jsonMock.mock.calls[0][2].signal as AbortSignal;
    page.rerender(<DiscoveryOfficialDirectory ranking kind="industry" search="browse=hot&disc_kind=industry&disc_code=400000.TI" scope="industry-owner" onTarget={target} onNavigate={navigate}/>);
    await screen.findByRole("button",{name:"查看正式行业0成分"});
    expect(signal.aborted).toBe(true);
    await act(async()=>finish({data:rankingReply()}));
    expect(screen.queryByRole("button",{name:"查看正式概念34成分"})).toBeNull();
    expect(target.mock.lastCall).toEqual(["industry-owner",{kind:"theme",code:"400000.TI",name:"正式行业0",catalogKind:"industry"}]);
  });
  it("完整涨幅排序和搜索仅消费同包原值，保留日期账户与正式下钻",async()=>{
    jsonMock.mockResolvedValue({data:rankingReply("concept",3)});
    render(<Harness ranking initial="browse=hot&date=2026-10-22&account=paper"/>);
    await screen.findByRole("button",{name:"查看正式概念2成分"});
    fireEvent.change(screen.getByRole("combobox",{name:"涨幅顺序"}),{target:{value:"asc"}});
    expect(resultRows()[0].textContent).toContain("正式概念0");
    fireEvent.change(screen.getByRole("textbox",{name:"搜索官方目录"}),{target:{value:"300001.TI"}});
    expect(resultRows()).toHaveLength(1);expect(jsonMock).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button",{name:"查看正式概念1成分"}));
    const query=new URL(navigate.mock.lastCall![0],"http://localhost").searchParams;
    expect(query.get("date")).toBe("2026-10-22");expect(query.get("account")).toBe("paper");
    expect(target.mock.lastCall?.[1]).toEqual({kind:"theme",code:"300001.TI",name:"正式概念1",catalogKind:"concept"});
  });
});

describe("官方目录与可选报价独立读取", () => {
  it("报价慢仍立即显示名称和代码并可下钻，原日期/名单不变，不等待额外行情",async()=>{
    let finish!: (value:unknown) => void;
    jsonMock.mockImplementation((url:string) => String(url).includes("/quotes?") ? new Promise(resolve => {finish=resolve;}) : Promise.resolve(route(url)));
    render(<Harness initial="browse=catalog&disc_kind=concept&date=2026-10-22&sec=daily&selection=600519&account=paper"/>);
    const button = await screen.findByRole("button",{name:"查看正式概念0成分"});
    await waitFor(() => expect(quoteCalls()).toHaveLength(1));
    expect(resultRows()).toHaveLength(24);
    expect(screen.getByText("报价正在补充，可先浏览目录。")).toBeTruthy();
    expect(button).toHaveProperty("disabled",false);
    fireEvent.click(button);
    expect(target.mock.lastCall?.[1]).toEqual({kind:"theme", code:"300000.TI", name:"正式概念0", catalogKind:"concept"});
    const params = new URL(navigate.mock.lastCall![0],"http://localhost").searchParams;
    expect(params.get("date")).toBe("2026-10-22");expect(params.get("sec")).toBe("daily");expect(params.get("selection")).toBe("600519");expect(params.get("account")).toBe("paper");
    expect(quoteCodes(quoteCalls()[0])).toHaveLength(24);
    await act(async() => finish({data:quoteReply("concept",quoteCodes(quoteCalls()[0]))}));
    expect(within(resultRows()[0]).getByText("0.00%")).toBeTruthy();
    expect(screen.getByText(/已展开报价有值 24 \/ 24 · 报价时点未核，不是全市场榜/)).toBeTruthy();
    expect(jsonMock.mock.calls.every(([url]) => String(url).startsWith("/api/themes/catalog"))).toBe(true);
  });

  it("报价失败不隐藏目录，可只重试缺失报价，真实0与未知分开",async()=>{
    let reads=0;
    jsonMock.mockImplementation(async(url:string) => {
      if(!url.includes("/quotes?"))return route(url);
      if(++reads===1)throw new Error("snapshot offline");
      const parsed=new URL(url,"http://localhost"),codes=(parsed.searchParams.get("codes") ?? "").split(",");
      const result=quoteReply("concept",codes);result.rows=result.rows.filter(row => row.code !== codes[1]);result.missing_codes=[codes[1]];
      return {data:result};
    });
    render(<Harness/>);
    await screen.findByRole("button",{name:"查看正式概念0成分"});
    expect(await screen.findByText(/报价读取失败：snapshot offline；名称代码仍可浏览/)).toBeTruthy();
    expect(resultRows()).toHaveLength(24);
    fireEvent.click(screen.getByRole("button",{name:"重试缺失报价"}));
    await screen.findByText("部分报价未提供，缺数不补0。");
    expect(within(resultRows()[0]).getByText("0.00%")).toBeTruthy();
    expect(within(resultRows()[1]).queryByText("0.00%")).toBeNull();
    expect(quoteCalls()).toHaveLength(2);
    expect(jsonMock.mock.calls.filter(([url]) => String(url).startsWith("/api/themes/catalog?"))).toHaveLength(1);
  });

  it("只为新增展开代码排单并发48以内批次，可连续读到末条，不整包抓390",async()=>{
    let finish!: (value:unknown) => void, first=true;
    jsonMock.mockImplementation((url:string) => {
      if(!url.includes("/quotes?"))return Promise.resolve({data:catalog("concept",73)});
      if(first){first=false;return new Promise(resolve => {finish=resolve;});}
      return Promise.resolve(route(url));
    });
    render(<Harness/>);
    await screen.findByRole("button",{name:"查看正式概念0成分"});
    await waitFor(() => expect(quoteCalls()).toHaveLength(1));
    fireEvent.click(screen.getByRole("button",{name:"继续加载"}));fireEvent.click(screen.getByRole("button",{name:"继续加载"}));
    expect(resultRows()).toHaveLength(72);expect(quoteCalls()).toHaveLength(1);
    await act(async() => finish({data:quoteReply("concept",quoteCodes(quoteCalls()[0]))}));
    await waitFor(() => expect(quoteCalls()).toHaveLength(2));
    expect(quoteCalls().map(quoteCodes).map(codes => codes.length)).toEqual([24,48]);
    fireEvent.click(screen.getByRole("button",{name:"继续加载"}));
    await screen.findByText("已全部加载（73 个目录）");
    await waitFor(() => expect(quoteCalls()).toHaveLength(3));
    const requested=quoteCalls().flatMap(quoteCodes);
    expect(requested).toHaveLength(73);expect(new Set(requested).size).toBe(73);
    expect(quoteCalls().every(call => quoteCodes(call).length <= 48)).toBe(true);
  });

  it("基础异步挂载后沿原滚动owner下拉追加，不增加内层滚动条或重取目录",async()=>{
    jsonMock.mockImplementation(async(url:string)=>url.includes("/quotes?")?route(url):{data:catalog("concept",35)});
    const page=render(<Harness/>);
    await screen.findByText(/已展开报价有值 24 \/ 24/);
    const sentinel=page.container.querySelector<HTMLElement>(".h-px")!;
    vi.spyOn(sentinel,"getBoundingClientRect").mockReturnValue({...far,top:100,bottom:101});
    fireEvent.scroll(window);
    expect(resultRows()).toHaveLength(35);
    await screen.findByText(/已展开报价有值 35 \/ 35/);
    expect(quoteCalls().map(quoteCodes).map(codes=>codes.length)).toEqual([24,11]);
    expect(jsonMock.mock.calls.filter(([url])=>String(url).startsWith("/api/themes/catalog?"))).toHaveLength(1);
    expect(page.container.querySelector('[class*="overflow"]')).toBeNull();
  });

  it("空目录不发报价；不可用报价即使附带数值也不展示为有效快照",async()=>{
    let first=true;
    jsonMock.mockImplementation(async(url:string)=>{
      if(!url.includes("/quotes?")){if(first){first=false;return {data:catalog("concept",0)};}return route(url);}
      const parsed=new URL(url,"http://localhost"),reply=quoteReply("concept",(parsed.searchParams.get("codes")??"").split(","));
      return {data:{...reply,available:false,reason:"snapshot unavailable"}};
    });
    render(<Harness/>);
    expect(await screen.findByText("本次目录未返回条目，不据此判断市场没有板块。")).toBeTruthy();
    expect(quoteCalls()).toHaveLength(0);expect(screen.queryByRole("button",{name:/报价/})).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"刷新目录"}));
    await screen.findByText(/报价读取失败：snapshot unavailable；名称代码仍可浏览/);
    expect(within(resultRows()[0]).queryByText("0.00%")).toBeNull();
    expect(within(resultRows()[0]).queryByText("1000.00")).toBeNull();
    expect(screen.getByRole("button",{name:"查看正式概念0成分"})).toHaveProperty("disabled",false);
    expect(quoteCalls()).toHaveLength(1);
  });

  it("搜索和名称排序复用已读报价，中文输入未完成不改URL，未知范围不冒充涨幅榜",async()=>{
    render(<Harness/>);
    await screen.findByText(/已展开报价有值 24 \/ 24/);
    const input=screen.getByRole("textbox",{name:"搜索官方目录"});
    fireEvent.compositionStart(input);fireEvent.change(input,{target:{value:"300001.TI"}});
    expect(navigate).not.toHaveBeenCalled();expect(resultRows()).toHaveLength(24);
    fireEvent.compositionEnd(input);
    expect(resultRows()).toHaveLength(1);expect(quoteCalls()).toHaveLength(1);
    fireEvent.change(screen.getByRole("combobox",{name:"目录顺序"}),{target:{value:"name"}});
    expect(quoteCalls()).toHaveLength(1);
    fireEvent.click(screen.getByRole("button",{name:"清除搜索"}));
    await waitFor(() => expect(quoteCalls()).toHaveLength(2));
    // Name order may expose unread names, but never rerequests successful codes.
    const all=quoteCalls().flatMap(quoteCodes);expect(new Set(all).size).toBe(all.length);
    expect(screen.queryByRole("option",{name:/涨幅|强度/})).toBeNull();
    expect(jsonMock.mock.calls.filter(([url]) => String(url).startsWith("/api/themes/catalog?"))).toHaveLength(1);
  });

  it("换kind取消旧报价，迟到数据不污染行业目录，父scope原样回报",async()=>{
    let finish!: (value:unknown) => void;
    jsonMock.mockImplementation((url:string) => url.includes("/quotes?") && url.includes("kind=concept") ? new Promise(resolve => {finish=resolve;}) : Promise.resolve(route(url)));
    const page=render(<DiscoveryOfficialDirectory kind="concept" search="disc_code=300000.TI" scope="parent-concept" onTarget={target} onNavigate={navigate}/>);
    await screen.findByRole("button",{name:"查看正式概念0成分"});await waitFor(() => expect(quoteCalls()).toHaveLength(1));
    const signal=quoteCalls()[0][2].signal as AbortSignal;
    page.rerender(<DiscoveryOfficialDirectory kind="industry" search="disc_code=400000.TI" scope="parent-industry" onTarget={target} onNavigate={navigate}/>);
    await screen.findByRole("button",{name:"查看正式行业0成分"});
    expect(signal.aborted).toBe(true);
    const expectedTarget=["parent-industry",{kind:"theme",code:"400000.TI",name:"正式行业0",catalogKind:"industry"}];
    // Catalog presence does not confirm that the parent passive effect has reported.
    await waitFor(() => expect(target.mock.lastCall).toEqual(expectedTarget));
    await act(async() => finish({data:quoteReply("concept",quoteCodes(quoteCalls()[0]))}));
    expect(screen.queryByRole("button",{name:"查看正式概念0成分"})).toBeNull();
    expect(screen.getByRole("list",{name:"官方目录结果"}).textContent).not.toContain("300000.TI");
    expect(target.mock.lastCall).toEqual(expectedTarget);
  });

  it("旧目录回包也不能覆盖新kind，首错可独立重试且不把失败当空目录",async()=>{
    let finish!: (value:unknown) => void;
    jsonMock.mockImplementationOnce(()=>new Promise(resolve=>{finish=resolve;})).mockRejectedValueOnce(new Error("catalog offline"));
    const page=render(<DiscoveryOfficialDirectory kind="concept" search="" onTarget={target} onNavigate={navigate}/>);
    await waitFor(()=>expect(jsonMock).toHaveBeenCalledTimes(1));
    const signal=jsonMock.mock.calls[0][2].signal as AbortSignal;
    page.rerender(<DiscoveryOfficialDirectory kind="industry" search="" onTarget={target} onNavigate={navigate}/>);
    expect((await screen.findByRole("alert")).textContent).toContain("官方目录读取失败");expect(signal.aborted).toBe(true);
    expect(quoteCalls()).toHaveLength(0);
    fireEvent.click(screen.getByRole("button",{name:"重试目录"}));
    await screen.findByRole("button",{name:"查看正式行业0成分"});
    await act(async()=>finish({data:catalog("concept")}));
    expect(screen.queryByRole("button",{name:"查看正式概念0成分"})).toBeNull();
  });

  it.each(["kind","source","duplicate","name"])("目录%s错配停止展示，不向下钻送错板块",async issue=>{
    const data=catalog();
    if(issue==="kind")data.kind="industry";
    if(issue==="source")(data as unknown as {source:string}).source="other";
    if(issue==="duplicate")data.items[1]=data.items[0];
    if(issue==="name")(data.items[0] as unknown as {name:unknown}).name={bad:true};
    jsonMock.mockResolvedValue({data});
    render(<Harness initial="browse=catalog&disc_kind=concept&disc_code=300000.TI"/>);
    expect((await screen.findByRole("alert")).textContent).toContain("官方目录身份或结构不符");
    expect(screen.queryByRole("button",{name:"查看正式概念0成分"})).toBeNull();expect(quoteCalls()).toHaveLength(0);
    expect(target.mock.calls.every(([,value])=>value===null)).toBe(true);
  });

  it.each(["kind","foreign_code","overlap"])("报价%s错配只停报价，正式目录仍可选",async issue=>{
    jsonMock.mockImplementation(async(url:string)=>{
      if(!url.includes("/quotes?"))return route(url);
      const parsed=new URL(url,"http://localhost"),codes=(parsed.searchParams.get("codes")??"").split(","),data=quoteReply("concept",codes);
      if(issue==="kind")data.kind="industry";
      if(issue==="foreign_code")data.rows[0].code="999999.TI";
      if(issue==="overlap")data.missing_codes=[codes[0]];
      return {data};
    });
    render(<Harness/>);
    expect(await screen.findByText(/报价读取失败：报价批次/)).toBeTruthy();
    expect(screen.getByRole("button",{name:"查看正式概念0成分"})).toHaveProperty("disabled",false);
    expect(within(resultRows()[0]).queryByText("0.00%")).toBeNull();
  });

  it("旧链接不以其他代码替代，卸载取消可选报价，不新增滚动容器",async()=>{
    jsonMock.mockImplementation((url:string)=>url.includes("/quotes?")?new Promise(()=>{}):Promise.resolve(route(url)));
    const page=render(<Harness initial="browse=catalog&disc_code=UNKNOWN"/>);
    expect(await screen.findByText("该代码不在本次正式目录，未换成同名板块。")).toBeTruthy();
    await waitFor(()=>expect(quoteCalls()).toHaveLength(1));
    const signal=quoteCalls()[0][2].signal as AbortSignal;
    expect(target.mock.calls.every(([,value])=>value===null)).toBe(true);
    expect(page.container.querySelector('[class*="overflow"]')).toBeNull();
    page.unmount();expect(signal.aborted).toBe(true);
  });

  it("读取增强选项只进URL不泄露为fetch参数，默认快照且保留显式full入口",async()=>{
    jsonMock.mockResolvedValue({data:{}});
    const controller=new AbortController();
    await getConceptDetail("884001.TI",{signal:controller.signal,catalogKind:"industry"});
    expect(jsonMock.mock.calls[0]).toEqual(["/api/themes/catalog/884001.TI/detail?kind=industry&enrichment=snapshot",undefined,{signal:controller.signal}]);
    await getConceptDetail("885517.TI",{enrichment:"full"});
    expect(jsonMock.mock.calls[1][0]).toBe("/api/themes/catalog/885517.TI/detail?kind=concept&enrichment=full");
    await getStockThemes("600519");await getStockThemes("600519",{enrichment:"full"});
    expect(jsonMock.mock.calls[2][0]).toBe("/api/themes/stock/600519?enrichment=snapshot");expect(jsonMock.mock.calls[3][0]).toBe("/api/themes/stock/600519?enrichment=full");
    await expect(getOfficialBoardQuotes("concept",Array.from({length:51},(_,index)=>`${300000+index}.TI`))).rejects.toThrow("每批须为1至50");
    expect(jsonMock).toHaveBeenCalledTimes(4);
  });
});
