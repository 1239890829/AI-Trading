/** InfoPanel 渲染测试：摘要徽章与降级兼容。
 *
 * 重点验证两件事：
 * 1. 有摘要数据时，重要度/情绪/关键数字/事实摘要都要渲染出来
 * 2. 老数据（无摘要字段）不能渲染出空白徽章——摘要字段全可选 */
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { InfoPanel, type InfoItem } from "./info-panel";

afterEach(cleanup);

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
  it("渲染重要度/情绪/关键数字与事实摘要", () => {
    render(<InfoPanel anns={[WITH_DIGEST]} news={[]} />);
    expect(screen.getByText("高")).toBeTruthy();
    expect(screen.getByText("偏负面")).toBeTruthy();
    // 数字串在 numbers 徽章（title 为完整数字列表）与摘要正文里各出现一次，
    // 用 /922\.78亿元/ 会命中多个元素，必须分开精确断言
    expect(screen.getByTitle("1.30% 922.78亿元")).toBeTruthy();
    expect(screen.getByText(/营业总收入为922\.78亿元/)).toBeTruthy();
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
});
