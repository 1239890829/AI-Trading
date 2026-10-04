import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { CommandPalette } from "./command-palette";
afterEach(cleanup);
it("按关键词过滤且键盘选择导航；中文组合回车不触发", () => {
 render(<CommandPalette/>);
 fireEvent.keyDown(window,{key:"k",ctrlKey:true});
 const input=screen.getByRole("combobox");
 expect(document.activeElement).toBe(input);
 fireEvent.change(input,{target:{value:"不存在的任务"}});
 expect(screen.queryAllByRole("option")).toHaveLength(0);
 fireEvent.change(input,{target:{value:"持仓"}});
 const option=screen.getByRole("option");
 const clicked=vi.fn((event:Event)=>event.preventDefault());
 option.addEventListener("click",clicked);
 fireEvent.keyDown(input,{key:"Enter",isComposing:true});
 expect(clicked).not.toHaveBeenCalled();
 fireEvent.keyDown(input,{key:"Enter"});
 expect(clicked).toHaveBeenCalledTimes(1);
});
it("不在已有模态窗口内开启第二个命令面板", () => {
 render(<><div aria-modal="true"/><CommandPalette/></>);
 fireEvent.keyDown(window,{key:"k",ctrlKey:true});
 expect(screen.queryByRole("combobox")).toBeNull();
});

it("结果选择循环且退出把焦点交回入口", () => {
 render(<CommandPalette/>);
 const trigger=screen.getByRole("button",{name:"打开命令面板"});
 trigger.focus();fireEvent.click(trigger);
 const input=screen.getByRole("combobox");
 const choices=screen.getAllByRole("option");
 fireEvent.keyDown(input,{key:"ArrowUp"});
 expect(choices[choices.length-1].getAttribute("aria-selected")).toBe("true");
 fireEvent.keyDown(input,{key:"ArrowDown"});
 expect(choices[0].getAttribute("aria-selected")).toBe("true");
 fireEvent.keyDown(input,{key:"Escape"});
 expect(document.activeElement).toBe(trigger);
 expect(screen.queryByRole("combobox")).toBeNull();
});
