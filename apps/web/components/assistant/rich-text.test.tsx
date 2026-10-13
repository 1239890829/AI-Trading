import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { parseBlocks, RichText } from "@/components/assistant/rich-text";
import { createEntityMatcher, type EntityDict } from "@/lib/entity-links";

// 本文件每个用例都 render 同一段文本（"贵州茅台…"），不清理会让
// `getByRole` 命中上一个用例遗留的按钮而报 multiple elements
// （2026-09-11 加「个股+页签」用例时暴露；与 board-flow.test.tsx 同约定）。
afterEach(cleanup);

const dict: EntityDict = {
  stocks: [{ name: "贵州茅台", code: "600519" }],
  themes: ["存储芯片"],
};
const matcher = createEntityMatcher(dict);

describe("parseBlocks", () => {
  it("解析标题/列表/段落", () => {
    const blocks = parseBlocks("## 标题\n- 项目一\n- 项目二\n\n普通段落");
    expect(blocks).toEqual([
      { kind: "heading", level: 2, text: "标题" },
      { kind: "list", ordered: false, items: ["项目一", "项目二"] },
      { kind: "para", lines: ["普通段落"] },
    ]);
  });

  it("解析围栏代码块（语言标记丢弃）", () => {
    const blocks = parseBlocks("前文\n```js\nconst a = 1;\n```");
    expect(blocks).toEqual([
      { kind: "para", lines: ["前文"] },
      { kind: "code", content: "const a = 1;" },
    ]);
  });

  it("解析表格（表头 + 分隔行 + 数据）", () => {
    const blocks = parseBlocks("| 名称 | 代码 |\n| --- | --- |\n| 贵州茅台 | 600519 |");
    expect(blocks).toEqual([
      { kind: "table", header: ["名称", "代码"], rows: [["贵州茅台", "600519"]] },
    ]);
  });

  it("解析引用与有序列表", () => {
    const blocks = parseBlocks("> 提示\n1. 第一\n2. 第二");
    expect(blocks).toEqual([
      { kind: "quote", lines: ["提示"] },
      { kind: "list", ordered: true, items: ["第一", "第二"] },
    ]);
  });
});

describe("RichText", () => {
  const onNavigate = vi.fn();

  it("个股命中渲染为可点击按钮", () => {
    render(<RichText text="贵州茅台今天不错" matcher={matcher} onNavigate={onNavigate} />);
    const btn = screen.getByRole("button", { name: "贵州茅台" });
    fireEvent.click(btn);
    expect(onNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ type: "stock", code: "600519" }),
    );
  });

  it("题材命中渲染为题材按钮", () => {
    render(<RichText text="存储芯片板块走强" matcher={matcher} onNavigate={onNavigate} />);
    fireEvent.click(screen.getByRole("button", { name: "存储芯片" }));
    expect(onNavigate).toHaveBeenCalledWith(
      expect.objectContaining({ type: "theme", name: "存储芯片" }),
    );
  });

  it("加粗与行内代码渲染", () => {
    const { container } = render(
      <RichText text="**止损** 与 `T+1` 规则" matcher={matcher} onNavigate={onNavigate} />,
    );
    expect(container.querySelector("strong")?.textContent).toBe("止损");
    expect(container.querySelector("code")?.textContent).toBe("T+1");
  });

  it("代码块内不做实体识别", () => {
    const { container } = render(
      <RichText text={"```\n贵州茅台 600519\n```"} matcher={matcher} onNavigate={onNavigate} />,
    );
    expect(container.querySelectorAll("button")).toHaveLength(0);
  });

  // ---- 「个股 + 页签」组合（2026-09-11，P2-28②） ----

  it("组合命中时按钮仍是个股名，但 title 与回调都指向页签", () => {
    const onNav = vi.fn();
    render(
      <RichText text="可到贵州茅台的分时图看" matcher={matcher} onNavigate={onNav} />,
    );
    const btn = screen.getByRole("button", { name: "贵州茅台" });
    expect(btn.getAttribute("title")).toContain("分时图");
    fireEvent.click(btn);
    expect(onNav).toHaveBeenCalledWith(
      expect.objectContaining({
        type: "stock",
        code: "600519",
        key: "stock_minute",
        url: "/workbench?symbol=600519&ct=minute",
      }),
    );
  });

  it("无页签词时 title 仍是「个股详情」（不谎报落点）", () => {
    render(<RichText text="贵州茅台今天不错" matcher={matcher} onNavigate={onNavigate} />);
    expect(screen.getByRole("button", { name: "贵州茅台" }).getAttribute("title")).toContain(
      "点击打开个股详情",
    );
  });

  it("routes allowed Markdown context links through the consumer and preserves modified clicks", () => {
    const onNav = vi.fn();
    render(<RichText text="[涨停池](/tape?tab=limitup) [外链](https://example.com)" matcher={matcher} onNavigate={onNav} />);
    const link = screen.getByRole("link", {name: "涨停池"});
    expect(link.getAttribute("href")).toBe("/tape?tab=limitup");
    fireEvent.click(link);
    expect(onNav).toHaveBeenCalledWith(expect.objectContaining({type: "nav", url: "/tape?tab=limitup"}));
    onNav.mockClear();
    fireEvent.click(link, {ctrlKey: true});
    expect(onNav).not.toHaveBeenCalled();
    expect(screen.queryByRole("link", {name: "外链"})).toBeNull();
  });
});

it("long structured replies retain entities, tables, code and the complete reply without inventing a causal path",()=>{
  const onNav=vi.fn();
  const text="## 观察\n"+"尚未核实公司关联，不构成确定性判断。".repeat(90)+"\n\n## 核对\n贵州茅台\n\n| 金额 | 条件 |\n| --- | --- |\n| 12.36 | 尚未审议 |\n\n```\n600519 未确认\n```\n\n## 限制\n没有借壳计划。";
  render(<RichText text={text} matcher={matcher} onNavigate={onNav}/>);
  fireEvent.click(screen.getByRole("tab",{name:"核对"}));
  fireEvent.click(screen.getByRole("button",{name:"贵州茅台"}));
  expect(onNav).toHaveBeenCalledWith(expect.objectContaining({type:"stock",code:"600519"}));
  expect(document.querySelector("table")?.textContent).toContain("12.36尚未审议");
  expect(document.querySelector("pre")?.textContent).toBe("600519 未确认");
  fireEvent.click(screen.getByRole("button",{name:"完整回复"}));
  expect(screen.getByText("没有借壳计划。")).toBeTruthy();
  expect(screen.queryByRole("tabpanel")).toBeNull();
});

it("an indented heading cannot trap the fallback parser without consuming input",()=>{
  expect(parseBlocks("  ## 尚未核实\n\n正文")).toEqual([{kind:"para",lines:["## 尚未核实"]},{kind:"para",lines:["正文"]}]);
});

it("a new reply resets the reading position and full-reply mode without retaining the prior text",()=>{
  const onNav=vi.fn();
  const first="## 旧观察\n"+"此前尚未核实。".repeat(200)+"\n\n## 旧限制\n旧限制不能丢。";
  const second="## 新观察\n"+"当前金额 12.36，尚未审议。".repeat(100)+"\n\n## 新限制\n新限制不能省略。";
  const {rerender}=render(<RichText text={first} matcher={matcher} onNavigate={onNav}/>);
  fireEvent.click(screen.getByRole("tab",{name:"旧限制"}));
  fireEvent.click(screen.getByRole("button",{name:"完整回复"}));
  rerender(<RichText text={second} matcher={matcher} onNavigate={onNav}/>);
  expect(screen.getByRole("tab",{name:"新观察"}).getAttribute("aria-selected")).toBe("true");
  expect(screen.queryByText("旧限制不能丢。")).toBeNull();
  expect(screen.queryByText("新限制不能省略。")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"完整回复"}));
  expect(screen.getByText("新限制不能省略。")).toBeTruthy();
});
