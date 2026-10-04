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
