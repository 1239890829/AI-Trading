import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { NewsModal, type NewsModalItem } from "./news-modal";
import { SymbolDetailModalHost, SymbolDetailProvider } from "@/components/detail/symbol-detail-modal";
import { getNewsContent } from "@/lib/api";

const navigation = vi.hoisted(() => ({replace: vi.fn(), push: vi.fn(), close: vi.fn()}));
vi.mock("next/navigation", () => ({usePathname: () => "/workbench", useSearchParams: () => new URLSearchParams(), useRouter: () => navigation}));
vi.mock("@/lib/api", async () => ({...await vi.importActual<typeof import("@/lib/api")>("@/lib/api"), getNewsContent: vi.fn()}));
vi.mock("@/components/stock-detail", () => ({StockDetailPanel: ({symbol, chartTab}: {symbol: string; chartTab?: string}) => <p data-testid="news-security-probe" data-chart={chartTab ?? ""}>{symbol}</p>}));

const source = {title: "证券新闻", url: "https://example.com/article", date: "2026-10-09 10:00:00"};
function SourceHost() {
  const [item, setItem] = useState<NewsModalItem | null>(source);
  return <SymbolDetailProvider><NewsModal item={item} onClose={() => {navigation.close(); setItem(null);}} /><SymbolDetailModalHost /></SymbolDetailProvider>;
}

afterEach(() => {cleanup(); vi.clearAllMocks(); vi.unstubAllGlobals();});

it.each([["贵州茅台", ""], ["贵州茅台分时图", "minute"]])("工作台新闻实体%s关闭来源再弹证券详情，保留原页与页签意图", async (text, tab) => {
  vi.stubGlobal("fetch", vi.fn(async () => ({ok: true, json: async () => ({data: {stocks: [{name: "贵州茅台", code: "600519"}], themes: []}})})));
  vi.mocked(getNewsContent).mockResolvedValue({kind: "news", title: source.title, source_label: "来源", published: source.date, paragraphs: [text], blocks: [{type: "p", text}], truncated: false, url: source.url});
  render(<SourceHost />);
  const entity = await screen.findByRole("button", {name: "贵州茅台"});
  if (tab) expect(entity.getAttribute("title")).toContain("分时图");
  fireEvent.click(entity);
  const panel = await screen.findByTestId("news-security-probe");
  expect(panel.textContent).toBe("600519");
  expect(panel.getAttribute("data-chart")).toBe(tab);
  expect(navigation.close).toHaveBeenCalledOnce();
  expect(screen.queryByRole("dialog", {name: source.title})).toBeNull();
  expect(navigation.replace).not.toHaveBeenCalled();
  expect(navigation.push).not.toHaveBeenCalled();
});
