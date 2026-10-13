/** InfoPanel 渲染测试：摘要徽章与降级兼容。
 *
 * 重点验证两件事：
 * 1. 有摘要数据时，重要度/情绪/关键数字/事实摘要都要渲染出来
 * 2. 老数据（无摘要字段）不能渲染出空白徽章——摘要字段全可选 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { InfoPanel, type InfoItem } from "./info-panel";
import { getNewsContent } from "@/lib/api";

afterEach(cleanup);
vi.mock("@/lib/api", async () => ({
  ...await vi.importActual<typeof import("@/lib/api")>("@/lib/api"),
  getNewsContent: vi.fn(),
}));
beforeEach(() => { vi.mocked(getNewsContent).mockReset().mockResolvedValue({kind: "news", title: "测试原文", source_label: "公开来源", published: null, url: "https://example.com/1", paragraphs: [], blocks: [], truncated: false}); });

const WITH_DIGEST: InfoItem = {
  title: "贵州茅台2026年半年度报告摘要",
  date: "2026-08-15",
  url: "https://example.com/1",
  source: "eastmoney",
  type: "半年度报告摘要",
  importance: "高",
  importance_score: 40,
  importance_reasons: ["定期业绩(+40)"],
  sentiment: "偏负面",
  sentiment_reasons: ["下降"],
  digest: "公司营业总收入为922.78亿元，同比增长1.30%。",
  digest_source: "正文",
  numbers: ["1.30%", "922.78亿元"],
};

const LEGACY: InfoItem = {
  title: "旧数据：没有摘要字段",
  date: "2026-08-01",
  url: "https://example.com/2",
  source: "eastmoney",
};

describe("InfoPanel", () => {
  it("long digest controls stay beside the native title button and never open the article", async () => {
    const digest = Array.from({length: 12}, (_, index) => `第${index + 1}项条件：成交金额922.78亿元，增长1.30%，该数据为过去报告期数据，不代表未来结果。`).join("\n\n");
    const ancestor = vi.fn();
    const {container} = render(<div onClick={ancestor}><InfoPanel anns={[{...WITH_DIGEST, digest}]} news={[]} /></div>);
    expect(container.querySelector("button button")).toBeNull();
    fireEvent.click(screen.getByRole("button", {name: "完整原文"}));
    expect(screen.getByLabelText("事实摘要完整原文").textContent).toContain(digest);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(getNewsContent).not.toHaveBeenCalled();
    expect(ancestor).not.toHaveBeenCalled();
    fireEvent.click(screen.getByText(/2026-08-15 · eastmoney/));
    expect(await screen.findByRole("dialog")).toBeTruthy();
    expect(getNewsContent).toHaveBeenCalledTimes(1);
    expect(ancestor).not.toHaveBeenCalled();
  });

  it("渲染重要度/情绪/关键数字与事实摘要", () => {
    render(<InfoPanel anns={[WITH_DIGEST]} news={[]} />);
    expect(screen.getByText("高")).toBeTruthy();
    expect(screen.getByText("偏负面")).toBeTruthy();
    // 数字串在 numbers 徽章（title 为完整数字列表）与摘要正文里各出现一次，
    // 用 /922\.78亿元/ 会命中多个元素，必须分开精确断言
    expect(screen.getByTitle("1.30% 922.78亿元")).toBeTruthy();
    expect(screen.getByText(/营业总收入为922\.78亿元/)).toBeTruthy();
    expect(screen.getByText("摘要取自：正文")).toBeTruthy();
  });

  it("keeps the fourth numeric fact visible instead of leaving it only in a hover title", () => {
    render(<InfoPanel anns={[{...WITH_DIGEST, numbers: ["1.30%", "922.78亿元", "2026年", "第四项：2.80%"], digest_source: undefined}]} news={[]} />);
    expect(screen.getByText("1.30% 922.78亿元 2026年 第四项：2.80%")).toBeTruthy();
    expect(screen.getByText("摘要取自：未知")).toBeTruthy();
  });

  it("摘要依据进 title，可解释而非黑箱", () => {
    render(<InfoPanel anns={[WITH_DIGEST]} news={[]} />);
    expect(screen.getByTitle(/重要度依据：定期业绩/)).toBeTruthy();
    expect(screen.getByTitle(/情绪依据：下降/)).toBeTruthy();
  });

  it("无摘要字段的老数据不渲染空白徽章", () => {
    const { container } = render(<InfoPanel anns={[LEGACY]} news={[]} />);
    expect(container.textContent).toContain("旧数据：没有摘要字段");
    expect(screen.queryByText("高")).toBeNull();
    expect(screen.queryByText("偏负面")).toBeNull();
  });

  it("空列表显示占位文案", () => {
    render(<InfoPanel anns={[]} news={[]} />);
    expect(screen.getByText("暂无公告")).toBeTruthy();
    expect(screen.getByText("暂无新闻")).toBeTruthy();
  });

  it("source failures never masquerade as empty and only user retry invokes the callback", () => {
    const retry = vi.fn();
    const view = render(<InfoPanel anns={[]} news={null} annError="ann-offline" newsError="news-offline" onRetry={retry} />);
    expect(screen.getAllByRole("alert")).toHaveLength(2);
    expect(screen.queryByText("暂无公告")).toBeNull(); expect(screen.queryByText("暂无新闻")).toBeNull();
    expect(retry).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name: "重试资讯"})); expect(retry).toHaveBeenCalledTimes(1);
    view.rerender(<InfoPanel anns={[]} news={null} annError="ann-offline" newsError="news-offline" onRetry={retry} loading />);
    fireEvent.click(screen.getByRole("button", {name: "重试中…"})); expect(retry).toHaveBeenCalledTimes(1);
  });

  it("retained raw rows preserve source/date/digest while the failed source is marked", () => {
    render(<InfoPanel anns={[WITH_DIGEST]} news={[LEGACY]} annError="ann-offline" newsError="news-offline" />);
    expect(screen.getByText(/保留上次读取的公告/)).toBeTruthy(); expect(screen.getByText(/保留上次读取的新闻/)).toBeTruthy();
    expect(screen.getByText(/2026-08-15 · eastmoney/)).toBeTruthy(); expect(screen.getByText("2026-08-01 · eastmoney")).toBeTruthy();
    expect(screen.getByText(WITH_DIGEST.title)).toBeTruthy(); expect(screen.getByText(LEGACY.title)).toBeTruthy();
    expect(screen.getByTitle(/来源：正文/)).toHaveProperty("textContent", WITH_DIGEST.digest);
  });
});
