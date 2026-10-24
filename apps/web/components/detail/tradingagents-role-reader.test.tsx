import {cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, expect, it} from "vitest";
import {TRADING_AGENTS_ROLES, type TradingAgentsResearchReport} from "@/lib/api/agent";
import {TradingAgentsRoleReader} from "./tradingagents-role-reader";

const report = (): TradingAgentsResearchReport => ({symbol:"002121",version:1,state:"completed",run_id:"a".repeat(32),generated_at:"2026-10-23T17:00:00+08:00",as_of:"2026-10-23T15:00:00+08:00",generated_today:false,upstream_sha:"upstream",input_sha256:"b".repeat(64),method_sha256:"c".repeat(64),verification:"unverified_full_graph_research",production_eligible:false,review_state:"unreviewed",review_summary:{finite_failures:0,peer_review_required:7,source_verified:false,independent_human_gold:false},reports:{market_report:"行情原答",sentiment_report:"情绪原答",news_report:"消息原答",fundamentals_report:"公司原答",investment_plan:"研究原答",trader_investment_plan:"交易原答",final_trade_decision:"原最终报告，未给上涨概率"},roles:TRADING_AGENTS_ROLES.map((role,index)=>({role,call_id:index+1,output_sha256:"d".repeat(64),fields:{content:`${role}的原答依据，不是交易指令。`,rating:"原研究观察"}})),role_projection_state:"available"});
afterEach(cleanup);
it("makes all twelve original voices reachable in four compact stages, with original fields on demand",()=>{
  render(<TradingAgentsRoleReader report={report()} originals={<p>七份原报告入口</p>}/>);
  expect(screen.getByText("12 个角色原答 · 七份报告 · 待核")).toBeTruthy();
  for (const [stage,names] of [["四位分析",["行情","情绪","消息","公司"]],["多空辩论",["多方","空方","研究统筹"]],["交易规划",["交易研究"]],["风险复核",["积极检验","保守检验","中性检验","最终研究"]]] as const) {
    fireEvent.click(screen.getByRole("button",{name:new RegExp(stage)}));
    for(const name of names){fireEvent.click(screen.getByRole("tab",{name}));fireEvent.click(screen.getByRole("button",{name:`核对${name}原答`}));expect(screen.getByLabelText(`${name}角色原答`).textContent).toContain('"content"');}
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
