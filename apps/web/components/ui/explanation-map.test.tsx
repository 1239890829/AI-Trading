import {afterEach,expect,it,vi} from "vitest";
import {cleanup,fireEvent,render,screen,within} from "@testing-library/react";
import {ConditionList,ExplanationMap,FactCards,FactTimeline,NarrativeText,routeExplanationLink,type ExplanationStep,type ExplanationBounds} from "./explanation-map";

afterEach(cleanup);
const steps:ExplanationStep[]=[
  {id:"source",label:"来源消息",summary:"公告记录",content:<p>公告仅提出收购意向。</p>,icon:"source"},
  {id:"relation",label:"个股关联",summary:"待核关联",content:<p>资产关联尚未核实。</p>,icon:"relation"},
  {id:"risk",label:"关键条件",summary:"审议未完成",content:<p>尚需审议，不超过 12.36 亿元。</p>,icon:"risk"},
];
it("routes parallel lower-row and explicit non-hub links through free gutters without intersecting another card",()=>{
  // CSS-layout rect fixture: two columns, unequal first-row heights, and a centred lower card.
  for (const width of [320,390,1024]) {
    const mid=width/2;
    const rects:Record<string,ExplanationBounds>={
      center:{left:mid-96,right:mid+96,top:8,bottom:76},
      short:{left:8,right:mid-6,top:106,bottom:209},
      ip:{left:mid+6,right:width-8,top:106,bottom:187},
      state:{left:mid-73,right:mid+73,top:237,bottom:344},
    };
    for (const [from,to] of [["center","state"],["short","state"],["short","ip"],["short","center"]]) {
      const route=routeExplanationLink({link:{from,to,label:"原词并列",kind:"context"},rects,width,height:362})!;
      expect(route.d).toContain(" Q ");
      route.points.forEach(point=>{expect(point.x).toBeGreaterThanOrEqual(0);expect(point.x).toBeLessThanOrEqual(width);expect(point.y).toBeGreaterThanOrEqual(0);expect(point.y).toBeLessThanOrEqual(362);});
      for(let index=1;index<route.points.length;index++) {
        const a=route.points[index-1],b=route.points[index];
        for(const [id,rect] of Object.entries(rects)) {
          if(id===from||id===to) continue;
          const intersects=a.x===b.x ? a.x>rect.left&&a.x<rect.right&&Math.max(a.y,b.y)>rect.top&&Math.min(a.y,b.y)<rect.bottom : a.y>rect.top&&a.y<rect.bottom&&Math.max(a.x,b.x)>rect.left&&Math.min(a.x,b.x)<rect.right;
          expect(intersects,`${width}px ${from}→${to} must not cross ${id}`).toBe(false);
        }
      }
    }
    // A full-width first card blocks the central gutter; the hub must choose an outer lane.
    const single={...rects,short:{left:8,right:width-8,top:106,bottom:209}};
    const around=routeExplanationLink({link:{from:"center",to:"state",label:"资料类别"},rects:single,width,height:362})!;
    expect(around.points.some(point=>point.x===2||point.x===width-2)).toBe(true);
  }
});
it("avoids the first-row cards with captured EventOverview DOM bounds",()=>{
  // Captured from the isolated browser's actual EventOverview layout, not financial sample data.
  const width=577,height=326.8359375;
  const rects:Record<string,ExplanationBounds>={
    center:{left:181.5,right:395.5,top:8,bottom:87.09375},
    source:{left:8,right:278.5,top:117.09375,bottom:189.09375},
    interpretation:{left:298.5,right:569,top:117.09375,bottom:189.09375},
    targets:{left:8,right:278.5,top:217.09375,bottom:306.8359375},
    response:{left:298.5,right:569,top:217.09375,bottom:306.8359375},
  };
  for(const [from,to] of [["center","targets"],["center","response"],["source","targets"]]) {
    const route=routeExplanationLink({link:{from,to,label:"资料关系"},rects,width,height})!;
    for(let index=1;index<route.points.length;index++) {
      const a=route.points[index-1],b=route.points[index];
      for(const [id,rect] of Object.entries(rects)) {
        if(id===from||id===to) continue;
        const crosses=a.x===b.x ? a.x>rect.left&&a.x<rect.right&&Math.max(a.y,b.y)>rect.top&&Math.min(a.y,b.y)<rect.bottom : a.y>rect.top&&a.y<rect.bottom&&Math.max(a.x,b.x)>rect.left&&Math.min(a.x,b.x)<rect.right;
        expect(crosses,`${from}→${to} must stay outside captured ${id}`).toBe(false);
      }
    }
  }
});
it("starts with the relation overview and no long detail, then supports arrows Home End and Escape",()=>{
  render(<ExplanationMap label="驱动资料" steps={steps}/>);
  expect(screen.queryByRole("tabpanel")).toBeNull();
  expect(screen.queryByText("公告仅提出收购意向。")).toBeNull();
  const tabs=screen.getAllByRole("tab");
  expect(tabs.map(tab=>tab.tabIndex)).toEqual([0,-1,-1]);
  tabs[0].focus();fireEvent.keyDown(tabs[0],{key:"ArrowRight"});
  expect(document.activeElement).toBe(tabs[1]);
  expect(within(screen.getByRole("tabpanel")).getByText("资产关联尚未核实。")).toBeTruthy();
  fireEvent.keyDown(tabs[1],{key:"End"});expect(document.activeElement).toBe(tabs[2]);
  fireEvent.keyDown(tabs[2],{key:"ArrowDown"});expect(document.activeElement).toBe(tabs[0]);
  fireEvent.keyDown(tabs[0],{key:"ArrowUp"});expect(document.activeElement).toBe(tabs[2]);
  fireEvent.keyDown(tabs[2],{key:"Home"});expect(document.activeElement).toBe(tabs[0]);
  fireEvent.keyDown(screen.getByRole("tabpanel"),{key:"Escape"});
  expect(screen.queryByRole("tabpanel")).toBeNull();expect(document.activeElement).toBe(tabs[0]);
  expect(tabs.map(tab=>tab.getAttribute("aria-selected"))).toEqual(["false","false","false"]);
});
it("closes detail on a second node click or explicit close and returns keyboard focus",()=>{
  render(<ExplanationMap label="驱动资料" steps={steps}/>);
  const target=screen.getByRole("tab",{name:"关键条件"});
  fireEvent.click(target);expect(screen.getByRole("tabpanel")).toBeTruthy();
  fireEvent.click(target);expect(screen.queryByRole("tabpanel")).toBeNull();
  fireEvent.click(target);fireEvent.click(screen.getByRole("button",{name:"收起关键条件详情"}));
  expect(screen.queryByRole("tabpanel")).toBeNull();expect(document.activeElement).toBe(target);
});
it("resets identity and retires a removed active node without reopening it on reappearance",()=>{
  const result=render(<ExplanationMap label="驱动资料" identity="a" steps={steps}/>);
  fireEvent.click(screen.getByRole("tab",{name:"关键条件"}));
  result.rerender(<ExplanationMap label="驱动资料" identity="b" steps={steps}/>);
  expect(screen.queryByRole("tabpanel")).toBeNull();
  fireEvent.click(screen.getByRole("tab",{name:"关键条件"}));
  result.rerender(<ExplanationMap label="驱动资料" identity="b" steps={steps.slice(0,2)}/>);
  expect(screen.queryByRole("tabpanel")).toBeNull();
  result.rerender(<ExplanationMap label="驱动资料" identity="b" steps={steps}/>);
  expect(screen.queryByRole("tabpanel")).toBeNull();
});
it("shows supplied edge labels in either direction and never draws missing endpoints",()=>{
  render(<ExplanationMap label="核对关系" center={{id:"event",label:"解释版本",value:"待核"}} steps={steps} links={[{from:"source",to:"event",label:"版本引用的来源",kind:"recorded"},{from:"event",to:"relation",label:"关联待核",kind:"unverified"},{from:"missing",to:"risk",label:"虚构关联",kind:"recorded"}]}/>);
  expect(screen.getByRole("tablist",{name:"核对关系节点"})).toBeTruthy();
  expect(screen.getByText("版本引用的来源")).toBeTruthy();expect(screen.getByText("关联待核")).toBeTruthy();
  expect(screen.queryByText("虚构关联")).toBeNull();
  expect(screen.getByText("关联待核").getAttribute("data-kind")).toBe("unverified");
  expect(screen.getByText("要点直接展示，点节点核对完整依据。连线展示资料关系，不证明因果。")).toBeTruthy();
});
it("refreshes current evidence and keeps nested actions independent from surrounding click handlers",()=>{
  const parent=vi.fn(),action=vi.fn();
  const result=render(<div onClick={parent}><ExplanationMap label="核对" steps={[{id:"source",label:"来源",content:<><button onClick={action}>本地操作</button><a href="https://example.com" onClick={event=>event.preventDefault()}>查看原文</a></>}]}/></div>);
  fireEvent.click(screen.getByRole("tab",{name:"来源"}));fireEvent.click(screen.getByRole("button",{name:"本地操作"}));
  fireEvent.click(screen.getByRole("link",{name:"查看原文"}));expect(action).toHaveBeenCalledTimes(1);expect(parent).not.toHaveBeenCalled();
  result.rerender(<ExplanationMap label="核对" steps={[{id:"source",label:"来源",content:<p>来源已撤回，不能按原消息解读。</p>}]}/>);
  fireEvent.click(screen.getByRole("tab",{name:"来源"}));
  expect(screen.queryByRole("link")).toBeNull();expect(screen.getByText("来源已撤回，不能按原消息解读。")).toBeTruthy();
});
it("keeps every critical condition and exact negation in visible cards",()=>{
  const conditions=["仅签订意向协议。","尚未完成审议。","金额不超过 12.36 亿元。","本年度不改主业。","没有借壳计划。"];
  render(<ConditionList label="全部关键条件" items={conditions} tone="warning"/>);
  expect(screen.getAllByRole("listitem").map(item=>item.textContent)).toEqual(conditions);
});
it("keeps short original text directly readable without a graphic menu or required click",()=>{
  const text="  公司未生产粮食。\n原材料涨价可能挤压利润，并不等于公司受益。";
  render(<NarrativeText text={text} label="消息原文"/>);
  expect(screen.getByLabelText("消息原文").textContent).toBe(text);
  expect(screen.queryByRole("tab")).toBeNull();expect(screen.queryByRole("button")).toBeNull();
});
it("shows an unpunctuated original condition in full by default and clears the previous full mode on object change",()=>{
  const text="未审议且未落地金额不超过12.36亿元".repeat(30);
  const result=render(<NarrativeText text={text} label="条件" identity="a"/>);
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.getByLabelText("原文线索").querySelector("button")?.textContent).toBe(text);
  fireEvent.click(screen.getByRole("button",{name:"完整原文"}));
  expect(screen.getByLabelText("条件完整原文").querySelector("h4")?.nextElementSibling?.textContent).toBe(text);
  const next=text.replace("12.36","10.25");
  result.rerender(<NarrativeText text={next} label="条件" identity="b"/>);
  expect(screen.queryByLabelText("条件完整原文")).toBeNull();
  expect(screen.getByLabelText("原文线索").querySelector("button")?.textContent).toBe(next);
});
it("keeps supplied clocks and unknowns separate from versions scores and prices",()=>{
  render(<><FactTimeline label="时点记录" items={[{label:"来源发布",value:"未知，未用收录时间补填",kind:"time"},{label:"系统收到",value:"2026-10-13 09:21:03",kind:"time"},{label:"解释生效",value:"尚未核实",kind:"time"},{label:"来源评分",value:"3/5",kind:"fact"}]}/><FactCards label="报价记录" items={[{label:"价格",value:"12.36"}]}/></>);
  const track=screen.getByLabelText("时点记录").querySelector("dl")!;
  expect([...track.querySelectorAll("dd")].map(node=>node.textContent)).toEqual(["未知，未用收录时间补填","2026-10-13 09:21:03","尚未核实"]);
  expect(screen.getByLabelText("记录信息").textContent).toContain("来源评分3/5");
  expect(within(screen.getByLabelText("时点记录")).getByText("按记录口径展示，位置不代表时间先后。")).toBeTruthy();
  expect(screen.getByLabelText("报价记录").textContent).toContain("价格12.36");
});
it("keeps the whole very long original recoverable rather than generating a replacement summary",()=>{
  const text=Array.from({length:90},(_,index)=>`条件 ${index}：金额不超过 ${index}.25 亿元，尚未审议。\n\n`).join("");
  render(<NarrativeText text={text} label="完整条件"/>);
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.getByText("条件 89：金额不超过 89.25 亿元，").closest("button")).toBeTruthy();
  expect(screen.queryByText(/查看其余/)).toBeNull();
  expect(screen.queryByRole("tabpanel")).toBeNull();
  const originalControl=screen.getByText("完整原文");
  expect(originalControl.tagName).toBe("BUTTON");
  fireEvent.click(originalControl);
  expect(screen.getByLabelText("完整条件完整原文").querySelector("h4")?.nextElementSibling?.textContent).toBe(text);
});
