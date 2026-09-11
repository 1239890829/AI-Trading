import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { Panel } from "@/components/panel";
import { PanelBoundary } from "@/components/ui/panel-boundary";

/**
 * 组件级错误边界（S2-6）。
 *
 * 核心承诺：**一个面板抛错，只有那一块降级** —— 页面其余内容与面板头部照常。
 * 此前只有路由级边界，任何面板渲染期抛错都会把整页换成错误卡。
 *
 * 注：项目未装 jest-dom，断言一律用 `toBeTruthy()` / `toBeNull()`
 * （与 index-cards.test.tsx 等既有测试同风格）。
 */

let consoleError: ReturnType<typeof vi.spyOn>;

beforeEach(() => {
  // React 会把被边界捕获的错误再打一遍到 console.error；边界自身也要留痕。
  // 测试里静音，断言留痕时单独取 mock 调用记录。
  consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
});

afterEach(() => {
  cleanup();
  consoleError.mockRestore();
});

function Boom({ flag }: { flag: { fail: boolean } }) {
  if (flag.fail) throw new Error("接口返回体不是预期结构");
  return <div>内容已恢复</div>;
}

describe("PanelBoundary", () => {
  it("正常时不介入，直接渲染 children", () => {
    render(
      <PanelBoundary label="测试面板">
        <div>正常内容</div>
      </PanelBoundary>,
    );
    expect(screen.getByText("正常内容")).toBeTruthy();
  });

  it("子组件抛错时就地降级，并把错误信息原文透出", () => {
    render(
      <PanelBoundary label="题材梯队">
        <Boom flag={{ fail: true }} />
      </PanelBoundary>,
    );

    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText(/题材梯队 · 局部加载失败/)).toBeTruthy();
    // 原文透出：排查时最需要的就是原始 message
    expect(screen.getByText(/接口返回体不是预期结构/)).toBeTruthy();
  });

  it("捕获后必须留痕（console.error），否则就是静默失败", () => {
    render(
      <PanelBoundary label="题材梯队">
        <Boom flag={{ fail: true }} />
      </PanelBoundary>,
    );
    expect(consoleError).toHaveBeenCalled();
    const joined = consoleError.mock.calls.flat().join(" ");
    expect(joined).toContain("PanelBoundary");
    expect(joined).toContain("题材梯队");
  });

  it("点重试可以恢复（上游修好后不必整页刷新）", () => {
    const flag = { fail: true };
    render(
      <PanelBoundary label="题材梯队">
        <Boom flag={flag} />
      </PanelBoundary>,
    );

    flag.fail = false;
    fireEvent.click(screen.getByRole("button", { name: "重试" }));

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("内容已恢复")).toBeTruthy();
  });

  it("resetKey 变化自动清除错误态（切标的/切 tab 不会钉死在错误卡上）", () => {
    const flag = { fail: true };
    const { rerender } = render(
      <PanelBoundary label="个股详情" resetKey="600519">
        <Boom flag={flag} />
      </PanelBoundary>,
    );
    expect(screen.getByRole("alert")).toBeTruthy();

    flag.fail = false;
    rerender(
      <PanelBoundary label="个股详情" resetKey="000001">
        <Boom flag={flag} />
      </PanelBoundary>,
    );

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("内容已恢复")).toBeTruthy();
  });

  it("resetKey 不变时保持错误态（不掩盖持续存在的故障）", () => {
    const flag = { fail: true };
    const { rerender } = render(
      <PanelBoundary label="个股详情" resetKey="600519">
        <Boom flag={flag} />
      </PanelBoundary>,
    );

    rerender(
      <PanelBoundary label="个股详情" resetKey="600519">
        <Boom flag={flag} />
      </PanelBoundary>,
    );

    expect(screen.getByRole("alert")).toBeTruthy();
  });
});

describe("Panel 集成（S2-6 的主收益）", () => {
  it("body 抛错时头部与页面其余部分照常，只有 body 降级", () => {
    render(
      <div>
        <div>页面其余内容</div>
        <Panel title="题材梯队" source="ths" quality="high">
          <Boom flag={{ fail: true }} />
        </Panel>
      </div>,
    );

    // 面板头部仍在 —— 用户能看出"是哪个面板坏了"
    expect(screen.getByText("题材梯队")).toBeTruthy();
    // 页面其余部分不受影响 —— 这是路由级边界做不到的
    expect(screen.getByText("页面其余内容")).toBeTruthy();
    // 只有 body 变成降级卡
    expect(screen.getByRole("alert")).toBeTruthy();
  });

  it("正常面板不因引入边界而变化", () => {
    render(
      <Panel title="题材梯队">
        <div>正常面板内容</div>
      </Panel>,
    );
    expect(screen.getByText("正常面板内容")).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
