import { useState } from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ModalShell } from "@/components/ui/modal-shell";

/**
 * 通用弹窗外壳（2026-09-15 统一）。
 *
 * 这个文件的判据是**全站弹窗的共同契约**——统一之前，5 个弹窗各写一遍，
 * 结果出现了「遮罩点击有的用 onMouseDown、有的用 onClick + stopPropagation」
 * 「role=dialog 有的挂遮罩、有的挂面板」这类不一致。现在这些行为只有一处实现，
 * 由本文件钉住；**新弹窗只要走 ModalShell，就自动满足这些不变量**。
 */

afterEach(cleanup);

function renderShell(props: Partial<React.ComponentProps<typeof ModalShell>> = {}) {
  const onClose = vi.fn();
  render(
    <ModalShell onClose={onClose} label="测试弹窗" {...props}>
      <p>正文内容</p>
    </ModalShell>,
  );
  return { onClose };
}

describe("ModalShell · 无障碍契约", () => {
  it("role=dialog / aria-modal / aria-label 都在**面板**上，testid 也挂在面板", () => {
    renderShell({ testid: "my-modal" });
    const dialog = screen.getByRole("dialog");
    expect(dialog.getAttribute("aria-label")).toBe("测试弹窗");
    expect(dialog.getAttribute("aria-modal")).toBe("true");
    // 统一后 testid 一律指向面板（此前有的弹窗挂在遮罩上，自动化查询语义不一致）
    expect(screen.getByTestId("my-modal")).toBe(dialog);
  });

  it("遮罩是 presentation（背景不应被当作另一个对话框）", () => {
    renderShell();
    expect(screen.getByRole("presentation")).toBeTruthy();
    expect(screen.getAllByRole("dialog")).toHaveLength(1);
  });
});

describe("ModalShell · 关闭路径", () => {
  it("Esc 关闭", () => {
    const { onClose } = renderShell();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("点遮罩关闭；点面板内部**不**关闭（无需调用方 stopPropagation）", () => {
    const { onClose } = renderShell();
    fireEvent.mouseDown(screen.getByRole("dialog"));
    expect(onClose).not.toHaveBeenCalled();

    fireEvent.mouseDown(screen.getByRole("presentation"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("头部关闭按钮按无障碍名可达（图标按钮必须有 aria-label）", () => {
    const { onClose } = renderShell({ header: <span>标题</span> });
    fireEvent.click(screen.getByLabelText("关闭"));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("无 header 时不渲染头部与关闭按钮（Esc / 遮罩仍可关）", () => {
    const { onClose } = renderShell();
    expect(screen.queryByLabelText("关闭")).toBeNull();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
  });
});

describe("ModalShell · 尺寸与层级", () => {
  it("尺寸档映射到不同容器类", () => {
    renderShell({ size: "sm" });
    expect(screen.getByRole("dialog").className).toContain("max-w-md");

    cleanup();
    renderShell({ size: "md" });
    expect(screen.getByRole("dialog").className).toContain("max-w-2xl");

    cleanup();
    renderShell({ size: "lg" });
    const lg = screen.getByRole("dialog").className;
    // lg 给的是**确定高度**（全功能面板内部靠 flex-1 撑开图表），不是 max-h
    expect(lg).toContain("h-[min(1000px,92vh)]");
    expect(lg).toContain("w-[min(1600px,96vw)]");
  });

  it("zIndex 可控（内容详情弹窗要压在其他弹窗之上）", () => {
    renderShell({ zIndex: 60 });
    expect(screen.getByRole("presentation").style.zIndex).toBe("60");

    cleanup();
    renderShell();
    expect(screen.getByRole("presentation").style.zIndex).toBe("50");
  });
});

describe("ModalShell · 内容槽", () => {
  it("header / footer 省略即不渲染；body 与 bodyTestId 正常传递", () => {
    renderShell();
    expect(screen.getByText("正文内容")).toBeTruthy();
    // 未传 bodyTestId ⇒ 无该属性，不产生空 testid
    expect(screen.getByRole("dialog").querySelector("[data-testid]")).toBeNull();
  });

  it("footer 存在时与 header 各自独立渲染", () => {
    renderShell({
      header: <span>标题区</span>,
      footer: <span>口径说明</span>,
      bodyTestId: "body-x",
    });
    expect(screen.getByText("标题区")).toBeTruthy();
    expect(screen.getByText("口径说明")).toBeTruthy();
    expect(screen.getByTestId("body-x")).toBeTruthy();
  });
});

describe("ModalShell 焦点与层级", () => {
  it("isolates the background, traps Tab and restores the originating focus", () => {
    const origin = document.createElement("button"); origin.textContent = "来源"; document.body.append(origin); origin.focus();
    const {unmount} = render(<ModalShell label="焦点测试" onClose={() => {}} header="标题"><button>首按钮</button><button>末按钮</button></ModalShell>);
    const dialog = screen.getByRole("dialog");
    expect(dialog.contains(document.activeElement)).toBe(true);
    expect(origin.inert).toBe(true);
    const last = screen.getByText("末按钮"); last.focus(); fireEvent.keyDown(window, {key: "Tab"});
    expect(document.activeElement).toBe(screen.getByLabelText("关闭"));
    unmount(); expect(origin.inert).not.toBe(true); expect(document.activeElement).toBe(origin); origin.remove();
  });
  it("Escape only closes the top nested overlay", () => {
    const closeLower = vi.fn(), closeTop = vi.fn();
    const lower = render(<ModalShell label="底层" onClose={closeLower} header="底层"><button>底层按钮</button></ModalShell>);
    const upper = render(<ModalShell label="上层" onClose={closeTop} zIndex={60} header="上层"><button>上层按钮</button></ModalShell>);
    fireEvent.keyDown(window, {key: "Escape"});
    expect(closeTop).toHaveBeenCalledTimes(1); expect(closeLower).not.toHaveBeenCalled();
    upper.unmount();
    expect(screen.getByRole("dialog", {name: "底层"}).contains(document.activeElement)).toBe(true);
    fireEvent.keyDown(window, {key: "Escape"}); expect(closeLower).toHaveBeenCalledTimes(1); lower.unmount();
  });
});


it("restores a keyboard SVG origin instead of dropping focus to the page", () => {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  const cell = document.createElementNS("http://www.w3.org/2000/svg", "g");
  cell.setAttribute("tabindex", "0"); svg.append(cell); document.body.append(svg); cell.focus();
  const {unmount} = render(<ModalShell label="SVG来源" onClose={() => {}} header="详情">内容</ModalShell>);
  expect(screen.getByRole("dialog").contains(document.activeElement)).toBe(true);
  unmount(); expect(document.activeElement).toBe(cell); svg.remove();
});

describe("ModalShell 可变阅读形态", () => {
  it("keeps a form and its input when switching between focus and peek", () => {
    const close = vi.fn();
    const background = document.createElement("button"); background.textContent = "背景动作"; document.body.append(background);
    const {unmount} = render(<ModalShell label="可变工具" presentation="drawer" size="lg" expandable onClose={close} header="工具"><label>草稿<input aria-label="草稿" defaultValue="初始" /></label></ModalShell>);
    expect(background.inert).toBe(true);
    const field = screen.getByLabelText("草稿");
    fireEvent.change(field, {target: {value: "尚未提交"}});
    fireEvent.click(screen.getByRole("button", {name: "并排旁览"}));
    expect(background.inert).toBe(false);
    expect(screen.getByRole("dialog").getAttribute("aria-modal")).toBeNull();
    background.focus();
    expect(document.activeElement).toBe(background);
    expect((screen.getByLabelText("草稿") as HTMLInputElement).value).toBe("尚未提交");
    fireEvent.click(screen.getByRole("button", {name: "展开工作区"}));
    expect(background.inert).toBe(true);
    expect(screen.getByLabelText("草稿")).toBe(field);
    expect(close).not.toHaveBeenCalled();
    unmount(); background.remove();
  });
});


describe("旁览与嵌套详情", () => {
  it("returns a nested modal to its actual background trigger", () => {
    function Example() {
      const [nested, setNested] = useState(false);
      return <><button onClick={() => setNested(true)}>原列表详情</button><ModalShell label="旁览工具" presentation="drawer" size="lg" expandable onClose={() => {}} header="工具">核对内容</ModalShell>{nested && <ModalShell label="证券详情" zIndex={60} onClose={() => setNested(false)} header="证券">详情内容</ModalShell>}</>;
    }
    render(<Example />);
    fireEvent.click(screen.getByRole("button", {name: "并排旁览"}));
    const trigger = screen.getByRole("button", {name: "原列表详情"});
    trigger.focus(); fireEvent.click(trigger);
    const nested = screen.getByRole("dialog", {name: "证券详情"});
    fireEvent.click(within(nested).getByRole("button", {name: "关闭"}));
    expect(document.activeElement).toBe(trigger);
  });
});
