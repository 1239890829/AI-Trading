import {afterEach, expect, it, vi} from "vitest";
import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {ConditionList, ExplanationMap, FactTimeline, NarrativeText, type ExplanationStep} from "./explanation-map";

afterEach(cleanup);
const steps: ExplanationStep[] = [
  {id:"source",label:"来源消息",content:<p>公告仅提出收购意向。</p>,icon:"source"},
  {id:"relation",label:"个股关联",content:<p>资产关联尚未核实。</p>,icon:"relation"},
  {id:"risk",label:"关键条件",content:<p>尚需审议，不超过 12.36 亿元。</p>,icon:"risk"},
];

it("uses one reading position with arrow, Home and End keyboard navigation", () => {
  render(<ExplanationMap label="驱动资料" steps={steps}/>);
  const tabs=screen.getAllByRole("tab");
  tabs[0].focus();
  fireEvent.keyDown(tabs[0],{key:"ArrowRight"});
  expect(document.activeElement).toBe(tabs[1]);
  expect(tabs[1].getAttribute("aria-selected")).toBe("true");
  expect(screen.queryByText("公告仅提出收购意向。")).toBeNull();
  expect(screen.getByRole("tabpanel").textContent).toBe("资产关联尚未核实。");
  fireEvent.keyDown(tabs[1],{key:"End"});
  expect(document.activeElement).toBe(tabs[2]);
  expect(screen.getAllByRole("tabpanel")).toHaveLength(1);
  expect(screen.getByRole("tabpanel").getAttribute("aria-labelledby")).toBe(tabs[2].id);
  fireEvent.keyDown(tabs[2],{key:"ArrowDown"});
  expect(document.activeElement).toBe(tabs[0]);
  fireEvent.keyDown(tabs[0],{key:"ArrowUp"});
  expect(document.activeElement).toBe(tabs[2]);
  fireEvent.keyDown(tabs[2],{key:"Home"});
  expect(document.activeElement).toBe(tabs[0]);
  expect(tabs.map(tab=>tab.tabIndex)).toEqual([0,-1,-1]);
});

it("resets on object identity changes and on removal of the active node without reviving it", () => {
  const result=render(<ExplanationMap label="驱动资料" identity="event-a" steps={steps}/>);
  fireEvent.click(screen.getByRole("tab",{name:"关键条件"}));
  result.rerender(<ExplanationMap label="驱动资料" identity="event-b" steps={steps.map(step=>({...step,content:<p>另一事件：{step.label}</p>}))}/>);
  expect(screen.getByRole("tabpanel").textContent).toBe("另一事件：来源消息");
  expect(screen.queryByText("尚需审议，不超过 12.36 亿元。")).toBeNull();
  fireEvent.click(screen.getByRole("tab",{name:"关键条件"}));
  result.rerender(<ExplanationMap label="驱动资料" identity="event-b" steps={steps.slice(0,2)}/>);
  expect(screen.getByRole("tabpanel").textContent).toBe("公告仅提出收购意向。");
  result.rerender(<ExplanationMap label="驱动资料" identity="event-b" steps={steps}/>);
  expect(screen.getByRole("tab",{name:"来源消息"}).getAttribute("aria-selected")).toBe("true");
});

it("updates current evidence in place and keeps links and actions independent from a surrounding row", () => {
  const parent=vi.fn(); const action=vi.fn();
  const result=render(<div onClick={parent}><ExplanationMap label="核对" steps={[{id:"source",label:"来源",content:<><button onClick={action}>本地操作</button><a href="https://example.com" onClick={event=>event.preventDefault()}>查看原文</a></>}]} /></div>);
  fireEvent.click(screen.getByRole("button",{name:"本地操作"}));
  expect(action).toHaveBeenCalledTimes(1);
  expect(parent).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("link",{name:"查看原文"}));
  expect(parent).not.toHaveBeenCalled();
  result.rerender(<ExplanationMap label="核对" steps={[{id:"source",label:"来源",content:<p>来源已撤回，不能按原消息解读。</p>}]}/>);
  expect(screen.queryByRole("link")).toBeNull();
  expect(screen.getByRole("tabpanel").textContent).toBe("来源已撤回，不能按原消息解读。");
});

it("keeps every condition including negations and exact amounts", () => {
  const conditions=["仅签订意向协议。","尚未完成审议。","金额不超过 12.36 亿元。","本年度不改主业。","没有借壳计划。"];
  render(<ConditionList label="全部关键条件" items={conditions} tone="warning"/>);
  expect(screen.getAllByRole("listitem").map(item=>item.textContent)).toEqual(conditions);
});

it("shows short original text without generating claims or a summary", () => {
  const text="  公司未生产粮食。\n原材料涨价可能挤压利润，并不等于公司受益。";
  render(<NarrativeText text={text} label="消息原文"/>);
  expect(screen.getByLabelText("消息原文").textContent).toBe(text);
  expect(screen.queryByRole("tab")).toBeNull();
});

it("lets every long-text fragment be read and restores the exact full original", () => {
  const text="本次仅为意向，不是最终交易。拟收购金额不超过 12.36 亿元，尚需股东大会批准。".repeat(8)+"\r\n\r\n"+"公司并未承诺注入资产，不能按已落地事件解读。".repeat(8)+"\n\n来源发布时间未知，系统收到不等于已生效。";
  render(<NarrativeText text={text} label="公告核对" identity="v1"/>);
  const tabs=screen.getAllByRole("tab");
  const originals=tabs.map(tab=>{fireEvent.click(tab); return screen.getByRole("tabpanel").textContent;});
  expect(originals.join("")).toBe(text);
  fireEvent.click(screen.getByRole("button",{name:"完整原文"}));
  expect(screen.getByLabelText("公告核对完整原文").querySelector("h4")?.nextElementSibling?.textContent).toBe(text);
  expect(screen.queryByRole("tabpanel")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"返回分段阅读"}));
  expect(screen.getByRole("tab",{name:"片段 1"}).getAttribute("aria-selected")).toBe("true");
});

it("does not split an unpunctuated condition or show a previous object's full-text state", () => {
  const text="未审议且未落地金额不超过12.36亿元".repeat(30);
  const result=render(<NarrativeText text={text} label="条件" identity="a"/>);
  expect(screen.getByLabelText("条件").textContent).toBe(text);
  const longer=text+"\n\n不得据此认为存在确定性收益。";
  result.rerender(<NarrativeText text={longer} label="条件" identity="a"/>);
  fireEvent.click(screen.getByRole("button",{name:"完整原文"}));
  result.rerender(<NarrativeText text={longer.replace("12.36","10.25")} label="条件" identity="b"/>);
  expect(screen.queryByLabelText("条件完整原文")).toBeNull();
  expect(screen.getByRole("button",{name:"完整原文"}).getAttribute("aria-pressed")).toBe("false");
  expect(screen.getByRole("tabpanel").textContent).toContain("10.25");
});

it("keeps supplied times and unknown source times distinct", () => {
  render(<FactTimeline label="时点记录" items={[{label:"来源发布",value:"未知，未用收录时间补填"},{label:"系统收到",value:"2026-10-13 09:21:03"},{label:"解释生效",value:"尚未核实"}]}/>);
  const timeline=screen.getByLabelText("时点记录");
  expect([...timeline.querySelectorAll("dd")].map(node=>node.textContent)).toEqual(["未知，未用收录时间补填","2026-10-13 09:21:03","尚未核实"]);
});

it("groups a very long original into at most six reading positions without losing paragraphs", () => {
  const text=Array.from({length:90},(_,index)=>`条件 ${index}：金额不超过 ${index}.25 亿元，尚未审议。\n\n`).join("");
  render(<NarrativeText text={text} label="完整条件"/>);
  const tabs=screen.getAllByRole("tab");
  expect(tabs.length).toBeLessThanOrEqual(6);
  expect(tabs.map(tab=>{fireEvent.click(tab);return screen.getByRole("tabpanel").textContent;}).join("")).toBe(text);
});
