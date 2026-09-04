import { describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { parseBlocks, RichText } from "@/components/assistant/rich-text";
import { createEntityMatcher, type EntityDict } from "@/lib/entity-links";

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
});
