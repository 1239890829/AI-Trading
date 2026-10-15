import {act,cleanup,fireEvent,render,screen,within} from "@testing-library/react";
import {afterEach,expect,it,vi} from "vitest";
import {LimitReason} from "./limit-reason";
import {ExplanationMap} from "@/components/ui/explanation-map";
import {getStockAttribution,getEventAttribution} from "@/lib/api";
vi.mock("@/lib/api",()=>({getStockAttribution:vi.fn(),getEventAttribution:vi.fn()}));
afterEach(()=>{cleanup();vi.clearAllMocks();});
it.each(["+","＋"])("treats parallel labels separated by %s as source text, never causal arrows", separator=>{
 const reason=["微短剧","影视IP","江西国资"].join(separator);
 render(<LimitReason reason={reason} symbol="002343" source="ths" date="2026-10-13"/>);
 expect(screen.getByLabelText("来源原因简述").textContent).toContain(reason);
 expect(screen.queryByRole("tab")).toBeNull();
 expect(screen.queryByLabelText("涨停原因资料关系")).toBeNull();
 expect(screen.getByText("来源解释，尚不能确认是涨停的真正原因")).toBeTruthy();
 fireEvent.click(screen.getByRole("button",{name:"完整原因与来源记录"}));
 expect(screen.getByLabelText("数据源归因原文").textContent).toContain(reason);
 expect(getStockAttribution).not.toHaveBeenCalled();
});
it("leaves missing attribution unknown without filling it from a concept or fetching",()=>{
 render(<LimitReason reason={null} symbol="600127" compact/>);
 expect(screen.getByText("数据源未提供涨停原因")).toBeTruthy();
 expect(screen.getByText("归因未知，不能用行业或概念标签补填。")).toBeTruthy();
 expect(screen.queryByRole("button")).toBeNull();expect(getStockAttribution).not.toHaveBeenCalled();
});
it("keeps the default compact while preserving a late denial, amounts and every condition in one evidence action",()=>{
 const reason="合成来源：拟收购设备公司。".repeat(20)+"金额不超过12.36亿元；本年度不改主业，没有借壳计划；尚未完成审计。";
 render(<LimitReason reason={reason} source="ths" date="2026-10-13"/>);
 expect(screen.getByLabelText("来源原因简述").textContent!.length).toBeLessThan(190);
 expect(screen.getByText("注意适用范围与限制")).toBeTruthy();
 expect(screen.queryByText(reason)).toBeNull();
 fireEvent.click(screen.getByRole("button",{name:"完整原因与来源记录"}));
 expect(screen.getByText(reason,{selector:"blockquote"}).textContent).toBe(reason);
 expect(getEventAttribution).not.toHaveBeenCalled();
});
it("only reads related saved evidence after the compact entry is explicitly opened",async()=>{
 vi.mocked(getStockAttribution).mockResolvedValue({schema_version:1,symbol:"600825",count:0,items:[]});
 render(<LimitReason reason="收购尚需审议+股权转让" symbol="600825" source="ths" date="2026-10-09" compact/>);
 expect(getStockAttribution).not.toHaveBeenCalled();
 fireEvent.click(screen.getByRole("button",{name:/涨停原因 · 查看依据/}));await act(async()=>{});
 const dialog=screen.getByRole("dialog",{name:"涨停原因原文"});
 expect(within(dialog).getByText("收购尚需审议+股权转让",{selector:"blockquote"})).toBeTruthy();
 expect(within(dialog).queryByRole("tab")).toBeNull();expect(getStockAttribution).toHaveBeenCalledTimes(1);
});
it("does not invent source or publication time from the trade day",()=>{
 render(<LimitReason reason="公司独立消息" date="2026-10-09"/>);
 expect(screen.getByText("来源未记录 · 2026-10-09")).toBeTruthy();
 fireEvent.click(screen.getByRole("button",{name:"完整原因与来源记录"}));
 expect(screen.getByText("交易日不代替新闻发布时间。")).toBeTruthy();
 expect(screen.queryByText(/同花顺|东方财富/)).toBeNull();
});
it("a legacy surrounding evidence node receives the original directly without nested entry or fetch",()=>{
 const text="拟收购，金额不超过12.36亿元；尚未审议；没有借壳计划。";
 render(<ExplanationMap label="入选依据核对" steps={[{id:"limit",label:"涨停原因",content:<LimitReason symbol="600825" reason={text} source="ths" date="2026-10-13" compact/>}]}/>);
 fireEvent.click(screen.getByRole("tab",{name:"涨停原因"}));
 const panel=screen.getByRole("tabpanel");expect(within(panel).getByText(text,{selector:"blockquote"})).toBeTruthy();
 expect(within(panel).queryByRole("button",{name:/查看依据/})).toBeNull();expect(getStockAttribution).not.toHaveBeenCalled();
});
it("retires a compact modal if its security or source reason changes",async()=>{
 vi.mocked(getStockAttribution).mockResolvedValue({schema_version:1,symbol:"600825",count:0,items:[]});
 const view=render(<LimitReason symbol="600825" reason="原原因" compact/>);
 fireEvent.click(screen.getByRole("button",{name:/查看依据/}));await act(async()=>{});
 expect(screen.getByRole("dialog")).toBeTruthy();view.rerender(<LimitReason symbol="600127" reason="新原因" compact/>);
 expect(screen.queryByRole("dialog")).toBeNull();expect(screen.getByText("原因摘录：新原因")).toBeTruthy();
});
