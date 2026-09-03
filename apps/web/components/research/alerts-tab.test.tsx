import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { AlertsTab } from "./alerts-tab";
import type { AlertEvent, AlertRule } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(cleanup);

vi.mock("next/link", () => ({
  // jsdom 下 App Router Link 依赖路由上下文，测试里降级为普通 <a> 即可断言 href
  default: ({ href, children, title }: { href: string; children: React.ReactNode; title?: string }) => (
    <a href={href} title={title}>{children}</a>
  ),
}));

vi.mock("@/lib/api", async () => {
  const actual = await vi.importActual<typeof import("@/lib/api")>("@/lib/api");
  return {
    ...actual,
    listAlertRules: vi.fn(),
    listAlertEvents: vi.fn(),
    getAlertChannels: vi.fn(),
  };
});

const mocked = await import("@/lib/api");

const rule: AlertRule = {
  id: 1,
  name: "茅台突破",
  condition_type: "price_above",
  threshold: 1300,
  scope: "symbols",
  symbols: ["600519"],
  cooldown_seconds: 300,
  channels: ["in_app"],
  enabled: true,
  last_triggered_at: null,
  created_at: "2026-09-01T08:00:00",
  updated_at: "2026-09-01T08:00:00",
};

const event: AlertEvent = {
  id: 9,
  rule_id: 1,
  symbol: "600519",
  trigger_value: 1302.5,
  threshold: 1300,
  triggered_at: "2026-09-01T10:30:00",
  acknowledged: false,
  delivered_channels: ["in_app"],
  snapshot: null,
};

describe("AlertsTab 触发记录跳转（切片 C）", () => {
  it("触发记录的标的是指向 workbench 详情页的链接", async () => {
    vi.mocked(mocked.listAlertRules).mockResolvedValue([rule]);
    vi.mocked(mocked.listAlertEvents).mockResolvedValue([event]);
    vi.mocked(mocked.getAlertChannels).mockResolvedValue({ available: ["in_app", "log"], default: ["in_app"] });

    render(<AlertsTab />);
    // 规则名在"规则列表"与"触发记录"两处都出现，直接等跳转链接挂载
    const link = await screen.findByTitle("查看行情详情");
    // 2026-09-03 起跳转带 from 返回参数（workbenchUrlWithBack），断言前缀而非全等
    expect(link.getAttribute("href")?.startsWith("/workbench?symbol=600519")).toBe(true);
    expect(link.textContent).toBe("600519");
  });
});

describe("AlertsTab 渠道配置状态（configured 诚实展示）", () => {
  it("未配置通道显示「未配置」徽标（title 说明后果），已配置的不显示", async () => {
    vi.mocked(mocked.listAlertRules).mockResolvedValue([]);
    vi.mocked(mocked.listAlertEvents).mockResolvedValue([]);
    vi.mocked(mocked.getAlertChannels).mockResolvedValue({
      available: ["in_app", "feishu"],
      default: ["in_app"],
      configured: { in_app: true, feishu: false },
    });

    const { container } = render(<AlertsTab />);
    const badge = await screen.findByTitle(
      "该通道未完成配置（如飞书 webhook），触发时会跳过并在后端日志告警，不会伪装成功",
    );
    expect(badge.textContent).toBe("未配置");
    // 全页只有这一个徽标，且挂在 feishu 的勾选项内（in_app 已配置不显示）
    expect(container.querySelectorAll("span[title]").length).toBe(1);
    expect(badge.closest("label")?.textContent).toContain("feishu");
  });

  it("后端未返回 configured 时不出徽标（缺失≠未配置，不臆测）", async () => {
    vi.mocked(mocked.listAlertRules).mockResolvedValue([]);
    vi.mocked(mocked.listAlertEvents).mockResolvedValue([]);
    vi.mocked(mocked.getAlertChannels).mockResolvedValue({
      available: ["in_app", "log"],
      default: ["in_app"],
    });

    const { container } = render(<AlertsTab />);
    await screen.findByText("log");
    expect(container.querySelector('span[title*="webhook"]')).toBeNull();
  });
});
