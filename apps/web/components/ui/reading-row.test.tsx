import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ReadingButton, ReadingRow } from "./reading-row";

afterEach(() => { cleanup(); window.getSelection()?.removeAllRanges(); });

describe("reading row interaction boundaries", () => {
  it("plain row space activates the same native title button once and retains its focus", () => {
    const open = vi.fn();
    const ancestor = vi.fn();
    render(<div onClick={ancestor}><ul><ReadingRow as="li" data-testid="row"><button type="button" data-reading-primary id="original-title" onClick={open}>事件标题</button><p>来源与时点</p></ReadingRow></ul></div>);
    const title = screen.getByRole("button", { name: "事件标题" });
    fireEvent.click(screen.getByText("来源与时点"));
    expect(open).toHaveBeenCalledTimes(1);
    expect(document.activeElement).toBe(title);
    expect(title.id).toBe("original-title");
    expect(screen.getByTestId("row").getAttribute("role")).toBeNull();
    expect(screen.getByTestId("row").getAttribute("tabindex")).toBeNull();
    expect(ancestor).not.toHaveBeenCalled();
    fireEvent.click(title);
    expect(open).toHaveBeenCalledTimes(2);
  });

  it("independent controls own click and Enter/Space without activating the article or its ancestor", () => {
    const open = vi.fn(), action = vi.fn(), ancestor = vi.fn(), ancestorKey = vi.fn();
    render(<div onClick={ancestor} onKeyDown={ancestorKey}><ReadingRow><button data-reading-primary onClick={open}>事件标题</button><button data-reading-action onClick={action}>关联个股</button><section data-reading-action><p>独立阅读区域</p></section></ReadingRow></div>);
    const control = screen.getByRole("button", {name: "关联个股"});
    fireEvent.click(control);
    const enter = new KeyboardEvent("keydown", {key: "Enter", bubbles: true, cancelable: true});
    const space = new KeyboardEvent("keydown", {key: " ", bubbles: true, cancelable: true});
    expect(control.dispatchEvent(enter)).toBe(true);
    expect(control.dispatchEvent(space)).toBe(true);
    expect(enter.defaultPrevented).toBe(false);
    expect(space.defaultPrevented).toBe(false);
    fireEvent.click(screen.getByText("独立阅读区域"));
    expect(action).toHaveBeenCalledTimes(1);
    expect(open).not.toHaveBeenCalled();
    expect(ancestor).not.toHaveBeenCalled();
    expect(ancestorKey).not.toHaveBeenCalled();
  });

  it("modified anchor clicks keep browser navigation defaults and never open the main detail", () => {
    const open = vi.fn(), ownClick = vi.fn();
    render(<ReadingRow><button data-reading-primary onClick={open}>事件标题</button><a data-reading-action href="#associated-stock" onClick={ownClick}><span>关联链接</span></a></ReadingRow>);
    const link = screen.getByRole("link", {name: "关联链接"});
    for (const modifiers of [{ctrlKey: true}, {metaKey: true}, {shiftKey: true}]) {
      const event = new MouseEvent("click", {bubbles: true, cancelable: true, ...modifiers});
      expect(link.dispatchEvent(event)).toBe(true);
      expect(event.defaultPrevented).toBe(false);
    }
    expect(ownClick).toHaveBeenCalledTimes(3);
    expect(open).not.toHaveBeenCalled();
    expect(link.getAttribute("href")).toBe("#associated-stock");
  });

  it("text selection and modified space clicks do not open an article", () => {
    const open = vi.fn();
    render(<ReadingRow data-testid="row"><button data-reading-primary onClick={open}>事件标题</button><p>完整依据可选择复制。</p></ReadingRow>);
    const text = screen.getByText("完整依据可选择复制。");
    const range = document.createRange();
    range.selectNodeContents(text);
    window.getSelection()?.addRange(range);
    fireEvent.click(text);
    expect(open).not.toHaveBeenCalled();
    window.getSelection()?.removeAllRanges();
    fireEvent.click(screen.getByTestId("row"), {ctrlKey: true});
    expect(open).not.toHaveBeenCalled();
  });

  it("a disabled title cannot dispatch a synthetic open", () => {
    const open = vi.fn();
    render(<ReadingRow data-testid="row"><button disabled data-reading-primary onClick={open}>不可用的事件</button><p>正文</p></ReadingRow>);
    fireEvent.click(screen.getByText("正文"));
    expect(open).not.toHaveBeenCalled();
  });

  it("standalone buttons retain native keyboard defaults and stop ancestor input", () => {
    const open = vi.fn(), ancestor = vi.fn(), key = vi.fn();
    render(<div onClick={ancestor} onKeyDown={key}><ReadingButton onClick={open}>相关事件</ReadingButton></div>);
    const button = screen.getByRole("button", {name: "相关事件"});
    fireEvent.keyDown(button, {key: "Enter"});
    fireEvent.keyDown(button, {key: " "});
    fireEvent.click(button);
    expect(open).toHaveBeenCalledTimes(1);
    expect(ancestor).not.toHaveBeenCalled();
    expect(key).not.toHaveBeenCalled();
  });
});
