import {afterEach,expect,it,vi} from "vitest";
import {cleanup,fireEvent,render,screen,within} from "@testing-library/react";
import {useState,type ReactNode} from "react";
import {ResearchClusterBadges,ResearchClustersPanel} from "./research-clusters";
import {clusterFixture} from "@/test/research-clusters-fixture";
import {unrecordedResearchClusters} from "@/lib/research-clusters";
vi.mock("@/components/stock-link",()=>({StockLink:({symbol,children}:{symbol:string;children:ReactNode})=><a href={`/workbench?symbol=${symbol}`}>{children}</a>}));
afterEach(cleanup);

it("distinguishes supported structure from candidate driver and retains partial original identities",()=>{
  const value=clusterFixture(),before=JSON.stringify(value);render(<ResearchClustersPanel data={value}/>);
  expect(screen.getByText("研究簇 · 当前参考")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:/人形机器人产业链/}));
  expect(screen.getByText("共同驱动仍为候选")).toBeTruthy();
  expect(screen.getByText("881126.TI")).toBeTruthy();
  expect(screen.getByText(/来源正式行业 · 仅部分关联/)).toBeTruthy();
  expect(screen.getByText("0.00%")).toBeTruthy();
  const detail=screen.getByText("证据 1 · 缺项 1 · 反证 1").closest("details")!;
  fireEvent.click(within(detail).getByText("证据 1 · 缺项 1 · 反证 1"));
  expect(within(detail).getByRole("link",{name:"结构公开说明"}).getAttribute("href")).toBe("https://example.com/structure");
  expect(screen.getByText(/2026-03-02（仅日期）/)).toBeTruthy();
  expect(JSON.stringify(value)).toBe(before);
});
it("keeps sheep narrative and all original tags, no invented code or combined board return",()=>{
  render(<ResearchClustersPanel data={clusterFixture()}/>);
  fireEvent.click(screen.getByRole("button",{name:/单股线索/}));
  fireEvent.click(screen.getByRole("button",{name:/“羊”字辈/}));
  expect(screen.getByText("来源原始题材标签 · 关系候选")).toBeTruthy();
  expect(screen.getAllByText("叙事候选").length).toBeGreaterThan(0);
  expect(screen.getAllByText(/“羊”字辈\+其他原始标签/).length).toBeGreaterThan(0);
  expect(screen.getByRole("link",{name:/000002/})).toBeTruthy();
  expect(screen.queryByText("881126.TI")).toBeNull();
  expect(screen.queryByText(/簇涨幅/)).toBeNull();
});
it("selection is local, supports reset and does not fetch per stock or when expanding evidence",()=>{
  const choose=vi.fn();const fetcher=vi.fn();vi.stubGlobal("fetch",fetcher);
  function Reader(){const[id,set]=useState<string|null>(null);return <ResearchClustersPanel data={clusterFixture()} selectedId={id} onSelect={value=>{choose(value);set(value);}} selectionSymbols={["000002"]}/>;}
  const view=render(<Reader/>);
  fireEvent.click(screen.getByRole("button",{name:/单股线索/}));
  choose.mockClear();
  const button=screen.getByRole("button",{name:/“羊”字辈.*名单1只/});button.focus();expect(document.activeElement).toBe(button);fireEvent.click(button);
  expect(button.getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByText("证据 1 · 缺项 1 · 反证 1"));
  fireEvent.click(screen.getByRole("button",{name:"全部原名单"}));
  expect(choose.mock.calls).toEqual([["research-sheep"],[null]]);expect(fetcher).not.toHaveBeenCalled();
  view.unmount();vi.unstubAllGlobals();
});
it("date/version transition retires the prior detail; historical absent, failed and empty remain distinct",()=>{
  const view=render(<ResearchClustersPanel data={clusterFixture()}/>);fireEvent.click(screen.getByRole("button",{name:/人形机器人产业链/}));
  view.rerender(<ResearchClustersPanel data={clusterFixture("2026-10-27")}/>);
  expect(screen.queryByText("共同驱动仍为候选")).toBeNull();
  view.rerender(<ResearchClustersPanel data={unrecordedResearchClusters("2026-10-23")} historical/>);
  expect(screen.getByText(/当时未记录研究簇/)).toBeTruthy();
  view.rerender(<ResearchClustersPanel error={new Error("offline")}/>);expect(screen.getByRole("alert").textContent).toContain("不能据此判断没有主题");
  view.rerender(<ResearchClustersPanel data={{...clusterFixture(),clusters:[]}}/>);expect(screen.getByText(/本次样本没有可列出的研究簇/)).toBeTruthy();
});
it("stock badges consume only supplied associations, including multiple themes without requests",()=>{
  const value=clusterFixture();value.clusters[1].stocks[0].symbol="000001";
  render(<ResearchClusterBadges data={value} symbol="000001"/>);
  const badges=within(screen.getByLabelText("研究簇关联"));expect(badges.getByText(/人形机器人产业链/)).toBeTruthy();expect(badges.getByText(/“羊”字辈/)).toBeTruthy();
});
it("partial branch interaction narrows only observed stocks and restores the complete cluster sample",()=>{
  const value=clusterFixture(),c=value.clusters[0];
  c.stocks.push({...c.stocks[0],symbol:"000003",name:"夹具乙"});c.unique_stock_count=2;c.branches[0].symbols.push("000003");
  render(<ResearchClustersPanel data={value}/>);fireEvent.click(screen.getByRole("button",{name:/人形机器人产业链/}));
  expect(screen.getByRole("link",{name:/夹具乙/})).toBeTruthy();
  const partial=screen.getByRole("button",{name:/汽车零部件.*881126.TI/});fireEvent.click(partial);
  expect(partial.getAttribute("aria-pressed")).toBe("true");
  expect(screen.queryByRole("link",{name:/夹具乙/})).toBeNull();
  expect(screen.getByText(/汽车零部件 · 观察股 1\/2只/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"全部分支"}));expect(screen.getByRole("link",{name:/夹具乙/})).toBeTruthy();
});
it("catalog ambiguity stays visible in the branch instead of becoming absent official identity",()=>{
  const value=clusterFixture();value.clusters[0].branches[1].official_identity=null;value.clusters[0].branches[1].catalog_mapping_state="ambiguous";
  render(<ResearchClustersPanel data={value}/>);fireEvent.click(screen.getByRole("button",{name:/人形机器人产业链/}));
  expect(screen.getByText(/原始标签 · 正式目录同名不唯一/)).toBeTruthy();expect(screen.queryByText("881126.TI")).toBeNull();
});
it("dense badges retain one structural and one narrative association plus an honest accessible remainder",()=>{
  const value=clusterFixture();value.clusters[1].stocks[0].symbol="000001";
  value.clusters.push({...value.clusters[1],id:"third",name:"另一交易叙事"},{...value.clusters[1],id:"fourth",name:"又一原始标签"});
  render(<ResearchClusterBadges data={value} symbol="000001"/>);
  const badge=screen.getByLabelText(/另2项研究关联/);expect(badge.textContent).toBe("+2关联");expect(badge.getAttribute("title")).toContain("另一交易叙事");
  expect(screen.queryByText("另一交易叙事 · 当前驱动候选")).toBeNull();
});
it("large single-stock label sets stay compressed, remain searchable and toggle locally without source reads",()=>{
  const value=clusterFixture(),sheep=value.clusters[1];
  sheep.stocks.push({...sheep.stocks[0],symbol:"000004"},{...sheep.stocks[0],symbol:"000005"});
  sheep.unique_stock_count=3;sheep.branches[0].symbols=["000002","000004","000005"];
  const singles=Array.from({length:145},(_,i)=>({...sheep,id:`single-${i}`,name:`原标签${i}`,unique_stock_count:1,stocks:[{...sheep.stocks[0],symbol:String(100000+i)}]}));
  value.clusters.push(...singles);
  const frozen=JSON.stringify(value),fetcher=vi.fn();vi.stubGlobal("fetch",fetcher);
  const view=render(<ResearchClustersPanel data={value}/>);
  const choices=within(screen.getByRole("group",{name:"研究簇切换"}));
  expect(choices.getAllByRole("button")).toHaveLength(3);
  expect(choices.getByRole("button",{name:/“羊”字辈.*样本3只/})).toBeTruthy();
  expect(choices.queryByRole("button",{name:/原标签144/})).toBeNull();
  const input=screen.getByRole("textbox",{name:"搜索全部原始题材标签"});input.focus();expect(document.activeElement).toBe(input);
  fireEvent.change(input,{target:{value:"原标签144"}});
  expect(choices.getByRole("button",{name:/原标签144/})).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"清除搜索"}));
  expect(choices.queryByRole("button",{name:/原标签144/})).toBeNull();
  const toggle=screen.getByRole("button",{name:"单股线索（145）"});fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
  expect(choices.getByRole("button",{name:/原标签0/})).toBeTruthy();
  expect(screen.getByText(/研究观察顺序，非强度排名/)).toBeTruthy();
  expect(fetcher).not.toHaveBeenCalled();expect(JSON.stringify(value)).toBe(frozen);
  view.unmount();vi.unstubAllGlobals();
});
