import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FilterMenu } from "./filter-menu";
import { ModalShell } from "./modal-shell";

afterEach(cleanup);

it("opens from the keyboard, selects one value and returns focus without a page change", async () => {
  const changed = vi.fn();
  function Example() {
    const [value, setValue] = useState("all");
    return <FilterMenu label="范围" value={value} options={[{key: "all", label: "全部"}, {key: "watch", label: "自选"}]} onChange={next => { setValue(next); changed(next); }} />;
  }
  render(<Example />);
  const trigger = screen.getByRole("button", {name: "范围：全部"});
  trigger.focus();
  fireEvent.keyDown(trigger, {key: "ArrowDown"});
  const selected = await screen.findByRole("menuitemradio", {name: "全部"});
  expect(selected.getAttribute("aria-checked")).toBe("true");
  fireEvent.click(screen.getByRole("menuitemradio", {name: "自选"}));
  expect(changed).toHaveBeenCalledExactlyOnceWith("watch");
  await waitFor(() => expect(document.activeElement).toBe(screen.getByRole("button", {name: "范围：自选"})));
  expect(screen.queryByRole("menu")).toBeNull();
  fireEvent.keyDown(trigger, {key: "ArrowDown"});
  const menu = await screen.findByRole("menu");
  fireEvent.keyDown(menu, {key: "Escape"});
  await waitFor(() => expect(screen.queryByRole("menu")).toBeNull());
  expect(changed).toHaveBeenCalledTimes(1);
  expect(document.activeElement).toBe(trigger);
});


it("keeps a nested menu in the drawer and gives Escape to the menu before the drawer", async () => {
  const close = vi.fn();
  render(<ModalShell label="规则面板" onClose={close} header={<h2>规则面板</h2>}>
    <FilterMenu label="条件" value="all" options={[{key:"all",label:"全部"},{key:"one",label:"单个"}]} onChange={vi.fn()}/>
  </ModalShell>);
  const trigger = screen.getByRole("button",{name:"条件：全部"});
  trigger.focus();
  fireEvent.keyDown(trigger,{key:"ArrowDown"});
  const menu = await screen.findByRole("menu");
  expect(screen.getByRole("dialog").contains(menu)).toBe(true);
  fireEvent.keyDown(menu,{key:"Escape"});
  await waitFor(()=>expect(screen.queryByRole("menu")).toBeNull());
  expect(close).not.toHaveBeenCalled();
  expect(document.activeElement).toBe(trigger);
  fireEvent.keyDown(trigger,{key:"Escape"});
  expect(close).toHaveBeenCalledTimes(1);
});
