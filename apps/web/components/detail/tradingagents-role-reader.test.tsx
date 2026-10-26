import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, expect, it} from "vitest";
import {TRADING_AGENTS_ROLES, type TradingAgentsResearchReport} from "@/lib/api/agent";
import {ResearchPassageReader, TradingAgentsRoleReader} from "./tradingagents-role-reader";

const report = (): TradingAgentsResearchReport => ({symbol:"002121",version:1,state:"completed",run_id:"a".repeat(32),generated_at:"2026-10-23T17:00:00+08:00",as_of:"2026-10-23T15:00:00+08:00",generated_today:false,upstream_sha:"upstream",input_sha256:"b".repeat(64),method_sha256:"c".repeat(64),verification:"unverified_full_graph_research",production_eligible:false,review_state:"unreviewed",review_summary:{finite_failures:0,peer_review_required:7,source_verified:false,independent_human_gold:false},reports:{market_report:"行情原答",sentiment_report:"情绪原答",news_report:"消息原答",fundamentals_report:"公司原答",investment_plan:"研究原答",trader_investment_plan:"交易原答",final_trade_decision:"原最终报告，未给上涨概率"},roles:TRADING_AGENTS_ROLES.map((role,index)=>({role,call_id:index+1,output_sha256:"d".repeat(64),fields:{content:`${role}的原答依据，不是交易指令。`,rating:"原研究观察"}})),role_projection_state:"available"});
afterEach(cleanup);
it("makes all twelve original voices reachable in four compact stages, with original fields on demand",()=>{
  render(<TradingAgentsRoleReader report={report()} originals={<p>七份原报告入口</p>}/>);
  expect(screen.getByText("12 个角色原答 · 七份报告 · 待核")).toBeTruthy();
  for (const [stage,names] of [["四位分析",["行情","情绪","消息","公司"]],["多空辩论",["多方","空方","研究统筹"]],["交易规划",["交易研究"]],["风险复核",["积极检验","保守检验","中性检验","最终研究"]]] as const) {
    fireEvent.click(screen.getByRole("button",{name:new RegExp(stage)}));
    for(const name of names){fireEvent.click(screen.getByRole("tab",{name}));fireEvent.click(screen.getByRole("button",{name:`核对${name}原答`}));expect(screen.getByLabelText(`${name}角色原答`).textContent).toContain("分析原文");expect(screen.getByLabelText(`${name}角色原答`).querySelector("pre")).toBeNull();}
  }
  fireEvent.click(screen.getByRole("button",{name:"七份报告"}));
  expect(screen.getByText("七份原报告入口")).toBeTruthy();
  expect(screen.queryByRole("tablist")).toBeNull();
});
it("uses keyboard focus without intercepting Chinese composition",()=>{
  render(<TradingAgentsRoleReader report={report()} originals={null}/>);
  fireEvent.click(screen.getByRole("button",{name:/四位分析/}));
  const market=screen.getByRole("tab",{name:"行情"});market.focus();
  fireEvent.keyDown(market,{key:"ArrowRight",isComposing:true});
  expect(market.getAttribute("aria-selected")).toBe("true");
  fireEvent.keyDown(market,{key:"End"});
  const company=screen.getByRole("tab",{name:"公司"});
  expect(company.getAttribute("aria-selected")).toBe("true");expect(document.activeElement).toBe(company);
});
it("keeps the original report reader when no role evidence is available",()=>{
  render(<TradingAgentsRoleReader report={{...report(),roles:undefined}} originals={<p>原七报告可读</p>}/>);
  expect(screen.getByText("原七报告可读")).toBeTruthy();expect(screen.queryByRole("tablist")).toBeNull();
});
it("lets readers inspect whole source passages and compare opposite voices without new analysis",()=>{
  const input=report();
  input.roles=input.roles!.map(output=>output.role==="Bull Researcher"?{...output,fields:{content:"收入增长。回款尚未核实，不能推断盈利质量改善。\n\n公司产品范围有公告依据。尚缺订单与收入占比。"}}:output.role==="Bear Researcher"?{...output,fields:{content:"现金流承压，但不足以认定坏账发生。"}}:output);
  render(<TradingAgentsRoleReader report={input} originals={null}/>);
  fireEvent.click(screen.getByRole("button",{name:/多空辩论/}));
  expect(screen.getByRole("tabpanel").textContent).toContain("收入增长。回款尚未核实，不能推断盈利质量改善。");
  expect(screen.queryByText("公司产品范围有公告依据。尚缺订单与收入占比。")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:/原文论据/}));
  fireEvent.click(screen.getByRole("button",{name:/原文第2段/}));
  expect(screen.getByText("公司产品范围有公告依据。尚缺订单与收入占比。")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"对照空方"}));
  expect(screen.getByLabelText("多方与空方角色对照").textContent).toContain("现金流承压，但不足以认定坏账发生。");
  fireEvent.click(screen.getByRole("button",{name:"转看空方论据"}));
  expect(screen.getByRole("tab",{name:"空方"}).getAttribute("aria-selected")).toBe("true");
  expect(screen.queryByLabelText("多方与空方角色对照")).toBeNull();
  expect(screen.getByRole("button",{name:/原文论据/}).getAttribute("aria-pressed")).toBe("true");
});

it("locates sentences in a long paragraph with adjacent qualifications and the exact whole source on demand",()=>{
  const sentences=Array.from({length:12},(_,index)=>`原文第${index+1}项描述收入线索，但现金回款、订单归属与最终盈利均未核实，不能据此形成确定结论。`);
  const text=sentences.join("");
  render(<ResearchPassageReader raw={`${text}\n\n后续完整段落仍未知。`} label="公司"/>);
  const focus=screen.getByLabelText("长段原句定位");
  expect(focus.textContent).toContain("不能将节选当作独立结论");
  expect(focus.textContent).toContain(sentences[0]);expect(focus.textContent).toContain(sentences[1]);
  expect(focus.textContent).not.toContain(sentences[7]);
  const first=screen.getByRole("button",{name:"定位原文第1句"});first.focus();
  fireEvent.keyDown(first,{key:"ArrowRight",isComposing:true});expect(first.getAttribute("aria-pressed")).toBe("true");
  fireEvent.keyDown(first,{key:"End"});
  expect(document.activeElement).toBe(screen.getByRole("button",{name:"定位原文第12句"}));
  expect(focus.textContent).toContain(sentences[10]);expect(focus.textContent).toContain(sentences[11]);
  fireEvent.click(screen.getByRole("button",{name:"核对本段完整原文"}));
  expect(screen.getByLabelText("本段完整原文").textContent).toBe(text);
  const paragraph=screen.getByRole("button",{name:/原文第1段/});paragraph.focus();fireEvent.keyDown(paragraph,{key:"ArrowRight"});
  expect(document.activeElement).toBe(screen.getByRole("button",{name:/原文第2段/}));
  expect(screen.queryByLabelText("长段原句定位")).toBeNull();
  expect(screen.getByText("后续完整段落仍未知。")).toBeTruthy();
});

it("turns a long source table into original-row selection while keeping every value and qualification",()=>{
  const raw="| 报告期 | 收入 | 原限制 |\n|---|---:|---|\n"+Array.from({length:12},(_,index)=>`| 原报告期${index+1} | ${index===11?"—":`21.${index}`} | 收入增长与盈利改善不是同一结论，现金回款与订单归属尚待核实，不能解释成确定买点 |`).join("\n");
  render(<ResearchPassageReader raw={raw} label="公司"/>);
  expect(screen.getByLabelText("原文表格定位").textContent).toContain("收入增长与盈利改善不是同一结论");
  expect(screen.queryByLabelText("长段原句定位")).toBeNull();
  fireEvent.change(screen.getByLabelText("报告期"),{target:{value:"11"}});
  expect(screen.getByLabelText("原文表格定位").textContent).toContain("—");
  expect(screen.getByLabelText("原文表格定位").textContent).not.toContain("21.0");
  fireEvent.click(screen.getByRole("button",{name:"核对本段完整原文"}));
  expect(screen.getByLabelText("本段完整原文").textContent).toBe(raw);
});
