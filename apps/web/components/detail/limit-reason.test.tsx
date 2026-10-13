import {act, cleanup, fireEvent, render, screen, within} from "@testing-library/react";
import {afterEach, expect, it, vi} from "vitest";
import {getEventAttribution, getStockAttribution} from "@/lib/api";
import {ExplanationMap} from "@/components/ui/explanation-map";
import {LimitReason} from "./limit-reason";

vi.mock("@/lib/api", () => ({getStockAttribution: vi.fn(), getEventAttribution: vi.fn()}));
afterEach(() => {cleanup(); vi.clearAllMocks();});

it("shows short original reasons by default and maps only their literal parallel labels", () => {
  const text = "拟收购财联社+重大资产重组";
  render(<LimitReason reason={text} source="ths" date="2026-10-09"/>);
  expect(screen.getByLabelText("涨停原因资料关系")).toBeTruthy();
  expect(screen.getByText(/同花顺 · 数据源归因 · 因果待核/)).toBeTruthy();
  expect(screen.queryByRole("tabpanel")).toBeNull();
  expect(within(screen.getByRole("region", {name:"数据源归因原文"})).getByText(text).tagName).toBe("BLOCKQUOTE");
  fireEvent.click(screen.getByRole("tab", {name: "拟收购财联社"}));
  expect(within(screen.getByRole("tabpanel", {name:"拟收购财联社"})).getByText(/没有建立标签之间的产业传导或涨停因果/)).toBeTruthy();
  expect(getStockAttribution).not.toHaveBeenCalled();
});

it("retains exact amounts and negations in a long reason without adding causal steps", () => {
  const text = "本次拟收购金额不超过 12.36 亿元，尚未完成审计。没有借壳计划；一年内不改变主业。".repeat(10);
  render(<LimitReason reason={text} source="eastmoney" date="2026-10-09"/>);
  expect(screen.queryByText(text)).toBeNull();
  const defaultClues = screen.getByRole("region", {name:"原文线索"});
  expect(defaultClues.textContent).toContain("不超过 12.36 亿元");
  expect(defaultClues.textContent).toContain("尚未完成审计");
  expect(defaultClues.textContent).toContain("没有借壳计划");
  expect(defaultClues.textContent).toContain("一年内不改变主业");
  expect(screen.queryByRole("tab")).toBeNull();
  fireEvent.click(screen.getByRole("button", {name:"完整原文"}));
  expect(screen.getByLabelText("数据源归因原文完整原文").textContent).toContain(text);
  expect(within(screen.getByLabelText("数据源归因原文完整原文")).getByText(text).textContent).toBe(text);
  fireEvent.click(screen.getByRole("button", {name:"返回要点阅读"}));
  expect(screen.queryByLabelText("数据源归因原文完整原文")).toBeNull();
  expect(screen.queryByRole("tabpanel")).toBeNull();
  expect(screen.getByLabelText("交易日记录").textContent).toContain("2026-10-09");
  expect(screen.queryByText(/上涨概率|已确认涨停因果|直接受益/)).toBeNull();
  expect(getStockAttribution).not.toHaveBeenCalled();
});

it("keeps missing attribution explicitly unknown without concept-based fallback or an evidence read", () => {
  render(<LimitReason reason={null} symbol="600127" compact/>);
  expect(screen.getByText("数据源未提供涨停原因")).toBeTruthy();
  expect(screen.getByText("归因未知，不能用行业或概念标签补填。")).toBeTruthy();
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.queryByRole("button")).toBeNull();
  expect(getStockAttribution).not.toHaveBeenCalled();
});

it("opens the compact overview without repeated evidence requests when its nodes change", async () => {
  vi.mocked(getStockAttribution).mockResolvedValue({schema_version: 1, symbol: "600825", count: 0, items: []});
  render(<LimitReason reason="收购尚需审议+股权转让" symbol="600825" source="ths" date="2026-10-09" compact/>);
  expect(getStockAttribution).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", {name: /涨停原因 · 查看原文/}));
  await act(async () => {});
  const dialog = screen.getByRole("dialog", {name: "涨停原因原文"});
  expect(within(dialog).queryByRole("tabpanel")).toBeNull();
  expect(within(dialog).getByText("收购尚需审议+股权转让", {selector:"blockquote"})).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("tab", {name:"收购尚需审议"}));
  expect(within(within(dialog).getByRole("tabpanel")).getByText("收购尚需审议", {selector:"blockquote"})).toBeTruthy();
  fireEvent.click(within(dialog).getByRole("tab", {name:"股权转让"}));
  expect(within(dialog).getByLabelText("交易日记录").textContent).toContain("2026-10-09");
  expect(getStockAttribution).toHaveBeenCalledTimes(1);
});

it("does not fabricate a source identity or publication clock from a trade date", () => {
  render(<LimitReason reason="公司独立消息" date="2026-10-09"/>);
  expect(screen.getByText(/来源未记录 · 数据源归因/)).toBeTruthy();
  expect(screen.queryByText(/同花顺|东方财富/)).toBeNull();
  expect(screen.getByLabelText("交易日记录").textContent).not.toContain("发布时间");
  expect(screen.getByText(/交易日不代替新闻发布时间/)).toBeTruthy();
});

it.each(["+", "＋"])("copies short parallel source labels using %s into distinct unverified branches", separator => {
  const labels = ["微短剧", "影视IP", "江西国资"];
  const reason = labels.join(separator);
  render(<LimitReason symbol="002343" reason={reason} source="ths" date="2026-10-13"/>);
  expect(screen.getByText("002343 · 涨停原因")).toBeTruthy();
  expect(screen.getAllByText("原文并列标签 · 待核")).toHaveLength(3);
  expect(screen.queryByRole("tabpanel")).toBeNull();
  for (const label of labels) {
    fireEvent.click(screen.getByRole("tab", {name: label}));
    const panel = screen.getByRole("tabpanel", {name: label});
    expect(within(panel).getByText(label, {selector: "blockquote"}).textContent).toBe(label);
    expect(within(panel).getByText(/没有建立标签之间的产业传导或涨停因果/)).toBeTruthy();
  }
  expect(within(screen.getByRole("region", {name:"数据源归因原文"})).getByText(reason).textContent).toBe(reason);
  expect(screen.getByLabelText("交易日记录").textContent).toContain("2026-10-13");
  expect(screen.getAllByText("2026-10-13")).toHaveLength(1);
  expect(getStockAttribution).not.toHaveBeenCalled();
});

it("opens the complete source reason in one surrounding node action without another overview, modal or read", () => {
  const text = "拟收购，金额不超过 12.36 亿元；尚未审议；没有借壳计划。";
  const view = render(<ExplanationMap label="入选依据核对" steps={[{id: "limit", label: "涨停原因", content: <LimitReason symbol="600825" reason={text} source="ths" date="2026-10-13" compact/>}]}/>);
  expect(screen.queryByText(text)).toBeNull();
  fireEvent.click(screen.getByRole("tab", {name: "涨停原因"}));
  const panel = screen.getByRole("tabpanel", {name: "涨停原因"});
  expect(within(panel).getByText(text, {selector: "blockquote"}).textContent).toBe(text);
  expect(within(panel).getByText("同花顺")).toBeTruthy();
  expect(within(panel).getByText("2026-10-13")).toBeTruthy();
  expect(within(panel).getByText("数据源归因，非已核实的涨停因果。")).toBeTruthy();
  expect(within(panel).queryByRole("button", {name: /查看原文/})).toBeNull();
  expect(within(panel).queryByRole("tab")).toBeNull();
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(getStockAttribution).not.toHaveBeenCalled();
  expect(getEventAttribution).not.toHaveBeenCalled();
  view.rerender(<ExplanationMap label="入选依据核对" steps={[{id: "limit", label: "涨停原因", content: <LimitReason symbol="600825" reason={null} compact/>}]}/>);
  expect(within(panel).queryByText(text)).toBeNull();
  expect(within(panel).getByText("数据源未提供涨停原因")).toBeTruthy();
  expect(within(panel).getByText("来源未记录")).toBeTruthy();
  expect(within(panel).getByText("未记录")).toBeTruthy();
  expect(getStockAttribution).not.toHaveBeenCalled();
  expect(getEventAttribution).not.toHaveBeenCalled();
});
