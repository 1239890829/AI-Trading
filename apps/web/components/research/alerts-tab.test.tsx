import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AlertsTab } from "./alerts-tab";
import type { AlertEvent, AlertRule } from "@/lib/api";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(() => {cleanup(); vi.useRealTimers(); vi.clearAllMocks(); vi.restoreAllMocks();});

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
    createAlertRule: vi.fn(),
    updateAlertRule: vi.fn(),
    deleteAlertRule: vi.fn(),
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

it("retains each blocked event's original reason and rule fallback without emitting any operation",async()=>{
  vi.mocked(mocked.listAlertRules).mockResolvedValue([rule]);
  vi.mocked(mocked.listAlertEvents).mockResolvedValue(Array.from({length:15},(_,index)=>({...event,id:index,triage:{verdict:"ignore",reason:`理由 ${index}：未确认关联，不是买入机会。`,model:"llm_fallback"}})));
  vi.mocked(mocked.getAlertChannels).mockResolvedValue({available:["in_app"],default:["in_app"]});
  render(<AlertsTab/>);
  const tabs=await screen.findAllByRole("tab");
  expect(tabs.length).toBeLessThanOrEqual(6);
  const reasons=tabs.map(tab=>{fireEvent.click(tab);return screen.getByRole("tabpanel").textContent;}).join("");
  expect(reasons).toContain("理由 14：未确认关联，不是买入机会。");
  expect(reasons).toContain("判读降级，规则回退");
  expect(mocked.createAlertRule).not.toHaveBeenCalled();
  expect(mocked.updateAlertRule).not.toHaveBeenCalled();
});

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


beforeEach(() => {
  vi.mocked(mocked.listAlertRules).mockReset().mockResolvedValue([rule]);
  vi.mocked(mocked.listAlertEvents).mockReset().mockResolvedValue([]);
  vi.mocked(mocked.getAlertChannels).mockReset().mockResolvedValue({available: ["in_app", "log"], default: ["in_app"]});
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  vi.mocked(mocked.createAlertRule).mockResolvedValue(rule);
  vi.mocked(mocked.updateAlertRule).mockResolvedValue(rule);
  vi.mocked(mocked.deleteAlertRule).mockResolvedValue(undefined);
});

describe("提醒管理 · 独立读取", () => {
  it("慢通道不阻塞规则和已触发记录", async () => {
    vi.mocked(mocked.getAlertChannels).mockReturnValueOnce(new Promise(() => {}));
    vi.mocked(mocked.listAlertEvents).mockResolvedValueOnce([event]);
    render(<AlertsTab />);
    expect(await screen.findByRole("button", { name: "删除规则 茅台突破" })).toBeTruthy();
    expect(screen.getByTitle("查看行情详情")).toBeTruthy();
    expect((screen.getByRole("button", { name: "创建规则" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("只轮询事件，规则和通道不每 10s 重读；事件失败保留旧记录且恢复", async () => {
    vi.useFakeTimers();
    vi.mocked(mocked.listAlertEvents).mockResolvedValueOnce([event])
      .mockRejectedValueOnce(new Error("events offline")).mockResolvedValue([event]);
    render(<AlertsTab />);
    await act(async () => { await vi.advanceTimersByTimeAsync(0); });
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(mocked.listAlertRules).toHaveBeenCalledTimes(1);
    expect(mocked.getAlertChannels).toHaveBeenCalledTimes(1);
    expect(screen.getByTitle("查看行情详情")).toBeTruthy();
    expect(screen.getByText(/触发记录读取失败.*上次读取结果/)).toBeTruthy();
    await act(async () => { await vi.advanceTimersByTimeAsync(10_000); });
    expect(screen.queryByText(/events offline/)).toBeNull();
    expect(mocked.listAlertEvents).toHaveBeenCalledTimes(3);
  });

  it("删除成功后独立校准规则和级联事件，不重复读通道", async () => {
    vi.mocked(mocked.listAlertEvents).mockResolvedValueOnce([event]).mockResolvedValue([]);
    vi.mocked(mocked.listAlertRules).mockResolvedValueOnce([rule]).mockResolvedValue([]);
    render(<AlertsTab />);
    fireEvent.click(await screen.findByRole("button", { name: "删除规则 茅台突破" }));
    fireEvent.click(screen.getByRole("button", { name: "确认删除" }));
    expect(await screen.findByText("暂无规则。")).toBeTruthy();
    expect(screen.getByText("暂无触发。")).toBeTruthy();
    expect(mocked.getAlertChannels).toHaveBeenCalledTimes(1);
    expect(mocked.deleteAlertRule).toHaveBeenCalledExactlyOnceWith(1);
  });

  it("事件首次失败保持未知，不污染成功规则，读源重试不执行任何命令", async () => {
    vi.mocked(mocked.listAlertEvents).mockRejectedValueOnce(new Error("events first offline")).mockResolvedValue([event]);
    render(<AlertsTab />);
    expect(await screen.findByText(/不能判断当前没有触发/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "删除规则 茅台突破" })).toBeTruthy();
    expect(screen.queryByText("暂无触发。")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "重试读取" }));
    expect(await screen.findByTitle("查看行情详情")).toBeTruthy();
    expect(mocked.createAlertRule).not.toHaveBeenCalled();
    expect(mocked.updateAlertRule).not.toHaveBeenCalled();
    expect(mocked.deleteAlertRule).not.toHaveBeenCalled();
  });
});

describe("提醒管理自定义选择与可撤确认", () => {
  it("规则失败保持未知，不污染独立成功的触发记录空集", async () => {
    vi.mocked(mocked.listAlertRules).mockRejectedValueOnce(new Error("offline"));
    render(<AlertsTab />);
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", expect.stringContaining("读取失败"));
    expect(screen.getByText(/当前是否为空尚未确认/)).toBeTruthy();
    expect(screen.queryByText("暂无规则。")).toBeNull();
    expect(screen.getByText("暂无触发。")).toBeTruthy();
    expect(screen.queryByText(/不能判断当前没有触发/)).toBeNull();
  });
  it("条件与范围通过键盘菜单选择，提交保留原scope/通道/冷却契约", async () => {
    const {container} = render(<AlertsTab />);
    await screen.findByRole("button", {name: "删除规则 茅台突破"});
    expect(container.querySelector("select")).toBeNull();
    fireEvent.change(screen.getByLabelText("名称"), {target: {value: "观察回落"}});
    fireEvent.keyDown(screen.getByRole("button", {name: "条件：现价 ≥"}), {key: "ArrowDown"});
    fireEvent.click(await screen.findByRole("menuitemradio", {name: "涨跌幅 ≤"}));
    fireEvent.change(screen.getByLabelText("阈值"), {target: {value: "-3"}});
    fireEvent.keyDown(screen.getByRole("button", {name: "范围：全部自选"}), {key: "ArrowDown"});
    fireEvent.click(await screen.findByRole("menuitemradio", {name: "指定标的"}));
    fireEvent.change(screen.getByLabelText("标的（逗号分隔）"), {target: {value: "600127, 603256"}});
    fireEvent.click(screen.getByRole("button", {name: "创建规则"}));
    expect(await screen.findByText(/规则已创建/)).toBeTruthy();
    expect(mocked.createAlertRule).toHaveBeenCalledWith({
      name: "观察回落", condition_type: "change_pct_below", threshold: -3, scope: "symbols",
      symbols: ["600127", "603256"], cooldown_seconds: 300, channels: ["in_app", "log"], enabled: true,
    });
  });

  it("删除先原位确认，可取消，未确认不会发出删除命令", async () => {
    const nativeConfirm = vi.spyOn(window, "confirm");
    render(<AlertsTab />);
    fireEvent.click(await screen.findByRole("button", {name: "删除规则 茅台突破"}));
    expect(screen.getByText(/该规则的触发历史也会删除/)).toBeTruthy();
    expect(mocked.deleteAlertRule).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", {name: "取消删除"}));
    expect(screen.queryByRole("button", {name: "确认删除"})).toBeNull();
    expect(mocked.deleteAlertRule).not.toHaveBeenCalled();
    expect(nativeConfirm).not.toHaveBeenCalled();
  });

  it("权限拒绝保留删除确认，且不把失败当成功", async () => {
    vi.mocked(mocked.deleteAlertRule).mockRejectedValueOnce(new Error("没有维护权限"));
    render(<AlertsTab />);
    fireEvent.click(await screen.findByRole("button", {name: "删除规则 茅台突破"}));
    fireEvent.click(screen.getByRole("button", {name: "确认删除"}));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "操作未完成：没有维护权限");
    expect(screen.getByRole("button", {name: "确认删除"})).toBeTruthy();
    expect(screen.queryByText(/已删除规则/)).toBeNull();
    expect(mocked.deleteAlertRule).toHaveBeenCalledExactlyOnceWith(1);
  });

  it("创建中锁定原控制，后端拒绝后保留用户草稿", async () => {
    let rejectCreate: (reason?: unknown) => void = () => {};
    vi.mocked(mocked.createAlertRule).mockImplementationOnce(() => new Promise((_resolve, reject) => {rejectCreate = reject;}));
    render(<AlertsTab />);
    await screen.findByRole("button", {name: "删除规则 茅台突破"});
    fireEvent.change(screen.getByLabelText("名称"), {target: {value: "保留草稿"}});
    fireEvent.click(screen.getByRole("button", {name: "创建规则"}));
    expect((screen.getByRole("button", {name: "创建中…"}) as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByLabelText("名称") as HTMLInputElement).closest("fieldset")?.disabled).toBe(true);
    rejectCreate(new Error("规则未获准"));
    expect(await screen.findByRole("alert")).toHaveProperty("textContent", "操作未完成：规则未获准");
    expect((screen.getByLabelText("名称") as HTMLInputElement).value).toBe("保留草稿");
    expect(screen.queryByText(/规则已创建/)).toBeNull();
  });
});


it("discloses rule fallback and does not turn a global event into a fake stock link", async () => {
  vi.mocked(mocked.listAlertRules).mockResolvedValue([rule]);
  vi.mocked(mocked.listAlertEvents).mockResolvedValue([{...event, symbol:"000000", triage:{verdict:"notify", reason:"AI unavailable", model:"llm_fallback"}}]);
  vi.mocked(mocked.getAlertChannels).mockResolvedValue({available:["in_app"], default:["in_app"]});
  render(<AlertsTab />);
  expect(await screen.findByText("规则提醒 · 本条未经过AI判读")).toBeTruthy();
  expect(screen.getByText("全局事件")).toBeTruthy();
  expect(screen.queryByTitle("查看行情详情")).toBeNull();
});


it("shows channel acceptance as distinct from delivery and from reading", async () => {
  vi.mocked(mocked.listAlertRules).mockResolvedValue([rule]);
  vi.mocked(mocked.listAlertEvents).mockResolvedValue([{...event, channel_states: [{channel: "feishu", state: "accepted", reason: "ok", created_at_ms: 1000, expires_at_ms: 2000, accepted_at_ms: 1500}]}]);
  vi.mocked(mocked.getAlertChannels).mockResolvedValue({available: ["in_app"], default: ["in_app"]});
  render(<AlertsTab />);
  expect(await screen.findByText(/渠道已受理（未确认送达）/)).toBeTruthy();
});
