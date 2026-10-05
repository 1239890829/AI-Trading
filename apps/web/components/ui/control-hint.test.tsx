import { afterEach, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ControlHint } from "./control-hint";

afterEach(cleanup);
it("keyboard help dismisses with Escape without moving focus or replacing the action", async () => {
  render(<ControlHint content="查找页面与工具"><button>打开命令面板</button></ControlHint>);
  const trigger = screen.getByRole("button", {name: "打开命令面板"});
  trigger.focus();
  fireEvent.focus(trigger);
  await screen.findByRole("tooltip");
  expect(trigger.getAttribute("aria-describedby")).toBeTruthy();
  fireEvent.keyDown(trigger, {key: "Escape"});
  await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());
  expect(document.activeElement).toBe(trigger);
});
it("opening a dialog removes existing help and does not resurrect it when the dialog closes", async () => {
  const result = render(<ControlHint content="浏览入口"><button>浏览工作区</button></ControlHint>);
  const trigger = screen.getByRole("button", {name: "浏览工作区"});
  trigger.focus(); fireEvent.focus(trigger);
  await screen.findByRole("tooltip");
  result.rerender(<ControlHint content="浏览入口" inactive><button>浏览工作区</button></ControlHint>);
  await waitFor(() => expect(screen.queryByRole("tooltip")).toBeNull());
  result.rerender(<ControlHint content="浏览入口"><button>浏览工作区</button></ControlHint>);
  expect(screen.queryByRole("tooltip")).toBeNull();
});
