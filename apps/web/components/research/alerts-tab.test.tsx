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
    expect(link.getAttribute("href")).toBe("/workbench?symbol=600519");
    expect(link.textContent).toBe("600519");
  });
});
