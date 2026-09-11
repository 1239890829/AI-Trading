import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render } from "@testing-library/react";
import { ParamsTab } from "./params-tab";

// 页面挂载即取数：把 api 层整体桩掉——本组测试只关心**容器与滚动的契约**，
// 不关心数据（数据展示另有断言价值时再单独测）。
vi.mock("@/lib/api", () => ({
  getAgentParams: vi.fn(async () => []),
  getAgentParamChanges: vi.fn(async () => []),
  getAgentParamSurvival: vi.fn(async () => ({
    decided: 0,
    applied: 0,
    rolled_back: 0,
    superseded: 0,
    still_effective: 0,
    survival_rate: null,
    insufficient: true,
    note: "",
    by_key: {},
    rollback_reasons: {},
    reason_labels: {},
  })),
  getAgentRollbackReasons: vi.fn(async () => []),
  createAgentParamChange: vi.fn(),
  applyAgentParamChange: vi.fn(),
  rollbackAgentParamChange: vi.fn(),
}));

afterEach(cleanup);

describe("参数配置页 · 滚动契约（2026-09-10 用户报「向下滚动不了」）", () => {
  /**
   * `FadeSwap` 的契约是「各 tab 根节点 `h-full min-h-0` + **自管滚动**」。
   *
   * 实测缺口（agent-browser 读真实几何）：三段全是 `shrink-0`、且根既没有
   * `overflow-y-auto` 也没有弹性子区块 ⇒ 内容 717px vs 容器 508px，多出的 209px
   * 交给父级 `overflow-hidden` 直接裁掉——**一像素都滚不动**，下半截看不见。
   * 给「变更单历史」留 `flex-1` 也不行：它会被前两段撑成 26px（内容 154px）。
   *
   * 所以正确形态是「根整体滚动 + 子段一律 shrink-0」。这两条断言锁住该形态，
   * 防止以后加内容时又滑回去（jsdom 无布局，只能断言 class 契约）。
   */
  it("根容器自管滚动，三段一律 shrink-0 且不占用弹性区", () => {
    const { container } = render(<ParamsTab />);
    const root = container.firstElementChild as HTMLElement;

    expect(root.className).toContain("h-full");
    expect(root.className).toContain("min-h-0");
    expect(root.className).toContain("flex-col");
    expect(root.className).toContain("overflow-y-auto");

    const sections = [...container.querySelectorAll("section")];
    expect(sections.length).toBeGreaterThan(0);
    for (const sec of sections) {
      expect(sec.className).toContain("shrink-0");
      // 弹性区会把同容器内的其他段压扁，本页三段都是「按内容高展示」的信息块
      expect(sec.className).not.toContain("flex-1");
    }
  });
});
