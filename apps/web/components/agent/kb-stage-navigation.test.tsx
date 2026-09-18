import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { getAgentKbFile, getAgentKbTree } from "@/lib/api";
import { KbBrowserTab } from "./kb-browser-tab";

vi.mock("@/lib/api", () => ({ getAgentKbTree: vi.fn(), getAgentKbFile: vi.fn() }));

const texts: Record<string, string> = {
  "retro-and-gaps.md": "### 6.0 阶段索引\n\n[通知阶段](stages/w02-notifications.md#imp-044)",
  "stages/w02-notifications.md": "# W02 通知\n\n[回总账](../retro-and-gaps.md#60-阶段索引)\n\n## IMP-044\n\n可靠投递任务",
};
const scroll = vi.fn();
const originalScroll = Object.getOwnPropertyDescriptor(HTMLElement.prototype, "scrollIntoView");

beforeEach(() => {
  vi.resetAllMocks();
  Object.defineProperty(HTMLElement.prototype, "scrollIntoView", { configurable: true, value: scroll });
  vi.mocked(getAgentKbTree).mockResolvedValue({
    root: "docs",
    files: Object.keys(texts).map((path) => ({
      path, name: path.split("/").at(-1)!, dir: path.includes("/") ? "stages" : "",
      tier: "current", size: texts[path].length, kb_ids: [],
    })),
  });
  vi.mocked(getAgentKbFile).mockImplementation(async (path) => ({ path, content: texts[path] }));
});

afterEach(() => {
  cleanup();
  if (originalScroll) Object.defineProperty(HTMLElement.prototype, "scrollIntoView", originalScroll);
  else Reflect.deleteProperty(HTMLElement.prototype, "scrollIntoView");
});

it("总账打开阶段任务，滚到任务标题，并可返回总账锚点", async () => {
  render(<KbBrowserTab />);
  fireEvent.click(await screen.findByRole("button", { name: "通知阶段" }));
  await screen.findByText("可靠投递任务");
  expect(getAgentKbFile).toHaveBeenLastCalledWith("stages/w02-notifications.md");
  await waitFor(() => expect((scroll.mock.instances.at(-1) as HTMLElement)?.getAttribute("data-md-anchor")).toBe("imp-044"));
  fireEvent.click(screen.getByRole("button", { name: "回总账" }));
  await screen.findByRole("button", { name: "通知阶段" });
  expect(getAgentKbFile).toHaveBeenLastCalledWith("retro-and-gaps.md");
  await waitFor(() => expect((scroll.mock.instances.at(-1) as HTMLElement)?.getAttribute("data-md-anchor")).toBe("60-阶段索引"));
});

it.each(["../retro-and-gaps.md", "docs/retro-and-gaps.md", "retro-and-gaps.md"])(
  "兼容相对路径及既有根路径 %s", async (link) => {
    vi.mocked(getAgentKbFile).mockImplementation(async (path) => ({
      path, content: path.startsWith("stages/") ? `[返回](${link})` : texts[path],
    }));
    render(<KbBrowserTab />);
    fireEvent.click(await screen.findByRole("button", { name: "通知阶段" }));
    fireEvent.click(await screen.findByRole("button", { name: "返回" }));
    await screen.findByRole("button", { name: "通知阶段" });
    expect(getAgentKbFile).toHaveBeenLastCalledWith("retro-and-gaps.md");
  },
);

it("不存在和编码损坏的文档链接不发出任意文件请求", async () => {
  vi.mocked(getAgentKbFile).mockResolvedValue({
    path: "retro-and-gaps.md",
    content: "[缺失](../../unknown.md) [坏链接](bad%.md) [外部](https://outside.invalid/x.md)",
  });
  render(<KbBrowserTab />);
  fireEvent.click(await screen.findByRole("button", { name: "缺失" }));
  fireEvent.click(screen.getByRole("button", { name: "坏链接" }));
  expect(screen.queryByRole("button", { name: "外部" })).toBeNull();
  expect(getAgentKbFile).toHaveBeenCalledTimes(1);
});

it("快速换文档时旧请求不能覆盖当前阶段内容", async () => {
  let finish!: (value: { path: string; content: string }) => void;
  vi.mocked(getAgentKbFile).mockImplementationOnce(() => new Promise((resolve) => { finish = resolve; }));
  render(<KbBrowserTab />);
  fireEvent.click(await screen.findByRole("button", { name: "w02-notifications.md" }));
  await screen.findByText("可靠投递任务");
  await act(async () => finish({ path: "retro-and-gaps.md", content: "过期总账正文" }));
  expect(screen.queryByText("过期总账正文")).toBeNull();
  expect(screen.getByText("可靠投递任务")).toBeTruthy();
});
