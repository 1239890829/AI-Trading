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

  it("label 变化自动清除错误态（默认行为，2026-09-13 §6.5b #4 起）", () => {
    const flag = { fail: true };
    const { rerender } = render(
      <PanelBoundary label="五档盘口">
        <Boom flag={flag} />
      </PanelBoundary>,
    );
    expect(screen.getByRole("alert")).toBeTruthy();

    flag.fail = false;
    rerender(
      <PanelBoundary label="逐笔成交">
        <Boom flag={flag} />
      </PanelBoundary>,
    );

    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("内容已恢复")).toBeTruthy();
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
 * `Panel.resetKey` 透传 + 错误态清除的**默认行为与显式途径**（D-3，2026-09-12；
 * 2026-09-13 §6.5b #4 把「title 变化」纳入默认清除）。
 *
 * 错误态自动清除的两条路（满足其一即可）：
 * - **默认**：`label`（= title 的字符串形态）变化——详情面板"同一块位置换内容"
 *   多数伴随标题变化（切 tab / 换面板），这从「调用点纪律」降为「默认行为」；
 * - **显式**：`resetKey` 变化——**title 不变**而内容换的场景（「自选股」各分组间
 *   title 完全相同）只有它判断得出来。
 *
 * 仍然钉住的形态（characterization，谁改了渲染结构导致结论翻转，这里会红）：
 * - 同实例换 title ⇒ 默认清除（原「不传就钉死」的调用点纪律已被默认行为覆盖）；
 * - 同 title 换 children ⇒ 仍需显式 resetKey；
 * - 并列条件渲染 ⇒ 两个 slot 位置不同，天然重挂载重置，不需要 resetKey；
 * - title 含计数的数据刷新 ⇒ 清一次错误态（成本已接受），持续失败时错误卡回归。
 */
describe("Panel.resetKey（D-3）", () => {
  it("同一 Panel 实例换内容（title 变）不传 resetKey ⇒ 默认清除（原「钉死」形态被默认行为覆盖）", () => {
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

    // label（title）变化即清错误态——§6.5b #4 拍板的默认行为；
    // 原先「不传 resetKey 就钉死 + 归因错位」的形态不再存在
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByText("内容已恢复")).toBeTruthy();
  });

  it("title 含计数的数据刷新（label 变）清一次错误态：子树被重试一次；仍在失败则错误卡回归", () => {
    let renders = 0;
    function AlwaysBoom(): never {
      renders += 1;
      throw new Error("仍在失败");
    }
    const { rerender } = render(
      <Panel title="自选股 (3)">
        <AlwaysBoom />
      </Panel>,
    );
    expect(screen.getByRole("alert")).toBeTruthy();
    const rendersBefore = renders;

    rerender(
      <Panel title="自选股 (4)">
        <AlwaysBoom />
      </Panel>,
    );

    // label 变了 ⇒ 错误态清一次、子树被重试（这是 §6.5b #4 已接受的成本；
    // 不钉具体渲染次数——StrictMode/边界重渲染会让计数漂移，只钉"确实重试了"）；
    // 但仍在失败 ⇒ 错误卡原样回归，不会被数据变化"擦白"
    expect(renders).toBeGreaterThan(rendersBefore);
    expect(screen.getByRole("alert")).toBeTruthy();
    expect(screen.getByText(/自选股 \(4\) · 局部加载失败/)).toBeTruthy();
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

