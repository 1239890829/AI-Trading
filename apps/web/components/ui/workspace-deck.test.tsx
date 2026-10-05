import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { WorkspaceDeck } from "./workspace-deck";
import { MAINTENANCE_TOOLS } from "@/lib/workspace-tools";

afterEach(cleanup);
describe("shared workspace tray", () => {
  it("keeps one group open and unmounts the previous tool choices", () => {
    const onOpen = vi.fn();
    render(<WorkspaceDeck tools={MAINTENANCE_TOOLS} onOpen={onOpen} />);
    fireEvent.click(screen.getByRole("button", {name: "设置与追踪"}));
    expect(screen.getAllByRole("region")).toHaveLength(1);
    expect(screen.getByRole("button", {name: /参数配置/})).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name: "运行与处置"}));
    expect(screen.queryByRole("button", {name: /参数配置/})).toBeNull();
    expect(screen.getAllByRole("region")).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", {name: /任务与回执/}));
    expect(onOpen.mock.calls[0][0]).toBe("tasks");
  });
  it("returns keyboard focus to the folder after Escape", () => {
    render(<WorkspaceDeck tools={MAINTENANCE_TOOLS} onOpen={vi.fn()} />);
    const folder = screen.getByRole("button", {name: "设置与追踪"});
    fireEvent.click(folder);
    const tool = screen.getByRole("button", {name: /参数配置/});
    tool.focus();
    fireEvent.keyDown(tool, {key: "Escape"});
    expect(screen.queryByRole("region")).toBeNull();
    expect(document.activeElement).toBe(folder);
    expect(folder.getAttribute("aria-expanded")).toBe("false");
  });
});

it("opens keyboard tool choices immediately and keeps focus recovery available", () => {
  const {container} = render(<WorkspaceDeck tools={MAINTENANCE_TOOLS} onOpen={vi.fn()} />);
  const folder = screen.getByRole("button", {name: "设置与追踪"});
  fireEvent.click(folder, {detail: 0});
  expect(container.querySelector('[data-motion-keyboard="true"]')).toBeTruthy();
  const tool = screen.getByRole("button", {name: /参数配置/});
  tool.focus();
  fireEvent.keyDown(tool, {key: "Escape"});
  expect(document.activeElement).toBe(folder);
  fireEvent.click(folder, {detail: 1});
  expect(container.querySelector('[data-motion-keyboard="true"]')).toBeNull();
});


it("finds tools across closed folders without invoking commands and clears back to the shelf", () => {
  const onOpen = vi.fn();
  render(<WorkspaceDeck tools={MAINTENANCE_TOOLS} onOpen={onOpen} />);
  const search = screen.getByRole("searchbox", {name: "查找工具"});
  fireEvent.change(search, {target: {value: "生效参数"}});
  expect(screen.queryByRole("button", {name: "设置与追踪"})).toBeNull();
  const result = screen.getByRole("button", {name: /参数配置/});
  expect(onOpen).not.toHaveBeenCalled();
  fireEvent.click(result);
  expect(onOpen.mock.calls[0][0]).toBe("params");
  fireEvent.change(search, {target: {value: "不存在的工具"}});
  expect(screen.getByRole("status").textContent).toContain("没有匹配");
  fireEvent.click(screen.getByRole("button", {name: "返回工具收纳"}));
  expect(document.activeElement).toBe(search);
  expect(screen.getByRole("button", {name: "设置与追踪"})).toBeTruthy();
});

it("clears inline search back to the open group and input without invoking a tool", () => {
  const onOpen = vi.fn();
  render(<WorkspaceDeck tools={MAINTENANCE_TOOLS} onOpen={onOpen} />);
  fireEvent.click(screen.getByRole("button", {name: "设置与追踪"}));
  const search = screen.getByRole("searchbox", {name: "查找工具"});
  fireEvent.change(search, {target: {value: "不存在"}});
  fireEvent.click(screen.getByRole("button", {name: "清空工具搜索"}));
  expect(document.activeElement).toBe(search);
  expect((search as HTMLInputElement).value).toBe("");
  expect(screen.getByRole("region", {name: "设置与追踪"})).toBeTruthy();
  expect(screen.queryByRole("button", {name: "清空工具搜索"})).toBeNull();
  expect(onOpen).not.toHaveBeenCalled();
});

it("leaves Escape to the input method during composition and can clear whitespace", () => {
  render(<WorkspaceDeck tools={MAINTENANCE_TOOLS} onOpen={vi.fn()} />);
  const search = screen.getByRole("searchbox", {name: "查找工具"}) as HTMLInputElement;
  fireEvent.change(search, {target: {value: "can"}});
  fireEvent.keyDown(search, {key: "Escape", isComposing: true});
  expect(search.value).toBe("can");
  fireEvent.keyDown(search, {key: "Escape", keyCode: 229});
  expect(search.value).toBe("can");
  fireEvent.keyDown(search, {key: "Escape"});
  expect(search.value).toBe("");
  fireEvent.change(search, {target: {value: "  "}});
  fireEvent.click(screen.getByRole("button", {name: "清空工具搜索"}));
  expect(search.value).toBe("");
});


it("keyboard input interrupts a pointer entrance so focused tools stay fully visible", () => {
 const {container,unmount}=render(<WorkspaceDeck tools={MAINTENANCE_TOOLS} onOpen={vi.fn()}/>);
 fireEvent.click(screen.getByRole("button",{name:"设置与追踪"}),{detail:1});
 const tool=screen.getByRole("button",{name:/参数配置/});
 tool.focus(); fireEvent.keyDown(tool,{key:"Tab"});
 expect(document.activeElement).toBe(tool);
 for(const row of container.querySelectorAll<HTMLElement>('.tool-launcher,.tool-tray')) {
  expect(row.style.opacity).toBe(""); expect(row.style.transform).toBe("");
 }
 unmount(); fireEvent(window,new Event("resize"));
});
