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

/**
 * `Panel.resetKey` 透传 + **哪些场景真的需要它**（2026-09-12 评审批次 2 / D-3）。
 *
 * 台账写的是「`resetKey` 生产 0 处传」，复核后发现**根因在 `Panel` 没有暴露该 prop**——
 * 全站都走 `Panel`，于是设计者写在 `panel-boundary` 里的契约（「`resetKey` 不是可选项」）
 * 在集成处**无法被兑现**。
 *
 * 但"哪里该传"不能靠感觉：需要它的前提是 **React 复用了同一个 `PanelBoundary` 实例**
 * （实例复用时 state 保留，错误态才不会被自动清掉）。下面两组用例把两类场景**钉开**：
 *
 * - **同一实例换内容**（`rightTab` 变 ⇒ `Panel` 的 title/children 变）⇒ **需要** `resetKey`；
 * - **并列条件渲染**（`{tab === "a" && <Panel/>}{tab === "b" && <Panel/>}`）⇒ 两个 slot
 *   位置不同，切 tab 时前者卸载、后者新挂载 ⇒ **天然重置，不需要** `resetKey`。
 *
 * 这两条是**现状钉住**（characterization）而非我的猜测——谁改了渲染结构导致结论翻转，
 * 这里会红，从而提醒重新判断"哪里该传"。
 */
describe("Panel.resetKey（D-3）", () => {
  it("同一 Panel 实例换内容时不传 resetKey ⇒ 停在错误卡上（这就是切 tab 被钉死的形态）", () => {
    const flag = { fail: true };
    const { rerender } = render(
      <Panel title="五档盘口">
        <Boom flag={flag} />
      </Panel>,
    );
    expect(screen.getByRole("alert")).toBeTruthy();

    flag.fail = false;
    rerender(
      <Panel title="逐笔成交">
        <Boom flag={flag} />
      </Panel>,
    );

    // 内容已经换成了「逐笔成交」，却仍显示错误卡，且 label 用的是**新** title
    // ⇒ 错误归因错位 + 该 tab 被钉死，直到整页刷新
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText(/逐笔成交 · 局部加载失败/)).toBeTruthy();
  });

  it("同一 Panel 实例换内容且传了 resetKey ⇒ 错误态随之清除", () => {
    const flag = { fail: true };
    const { rerender } = render(
      <Panel title="五档盘口" resetKey="book">
        <Boom flag={flag} />
      </Panel>,
    );
    expect(screen.getByRole("alert")).toBeTruthy();

    flag.fail = false;
    rerender(
      <Panel title="逐笔成交" resetKey="trades">
        <Boom flag={flag} />
      </Panel>,
    );

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("内容已恢复")).toBeTruthy();
  });

  it("title 不变、只有 children 换（工作台切分组的形态）⇒ 不传会钉死，传了才恢复", () => {
    const flag = { fail: true };
    // title 在「自选股」各分组间是**同一个字符串** ⇒ 光看 title 判断不出视图换了
    const view = (group: string, key?: string) => (
      <Panel title="自选股" resetKey={key}>
        {group === "全部" ? <Boom flag={flag} /> : <div>分组内容</div>}
      </Panel>
    );

    // ① 不传 resetKey：flag 已修好、视图也已切走，却仍停在错误卡上
    const a = render(view("全部"));
    expect(screen.getByRole("alert")).toBeTruthy();
    flag.fail = false;
    a.rerender(view("默认"));
    expect(screen.getByRole("alert")).toBeTruthy();
    a.unmount();

    // ② 传了 resetKey：切分组即清除错误态
    flag.fail = true;
    const b = render(view("全部", "全部"));
    expect(screen.getByRole("alert")).toBeTruthy();
    flag.fail = false;
    b.rerender(view("默认", "默认"));
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("分组内容")).toBeTruthy();
  });

  it("并列条件渲染的 Panel 切换会重挂载 ⇒ 天然重置，图表区因此无需 resetKey", () => {
    const flag = { fail: true };
    function Tabbed({ tab }: { tab: "kline" | "minute" }) {
      return (
        <div>
          {tab === "kline" && (
            <Panel title="K线">
              <Boom flag={flag} />
            </Panel>
          )}
          {tab === "minute" && (
            <Panel title="分时">
              <div>分时内容</div>
            </Panel>
          )}
        </div>
      );
    }

    const { rerender } = render(<Tabbed tab="kline" />);
    expect(screen.getByRole("alert")).toBeTruthy();

    flag.fail = false;
    rerender(<Tabbed tab="minute" />);

    // 两个 slot 位置不同 ⇒ 旧 Panel 卸载、新 Panel 挂载 ⇒ 不必传 resetKey 也已恢复。
    // 若这条变红，说明渲染结构被改成"同位置复用"，那图表区就必须补 resetKey。
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("分时内容")).toBeTruthy();
  });
});

