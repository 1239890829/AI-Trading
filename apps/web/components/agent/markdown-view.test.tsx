import fs from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render } from "@testing-library/react";

import { MarkdownView } from "./markdown-view";

// 块级解析器的**结构性守卫**（2026-09-12）。
//
// ## 被守的是什么
// `MarkdownView` 的块级循环里，最后那个「普通段落」分支是**兜底分支**：凡没被
// 围栏代码块 / 表格 / 标题 / 列表 / 引用 / 分隔线 / 空行接住的行，都落到它这里。
// 它的消费循环**必须无条件先吃一行**（`do...while`）——否则存在一类输入会让它
// **一行都不消费、`i` 永不推进 ⇒ 同步死循环 ⇒ 整页卡死**（循环在渲染期 `useMemo`
// 里，事件循环被占死，React 与浏览器都没有出路）。
//
// 触发形态：**以 `|` 开头、却不构成表格的行**。表格分支额外要求「下一行是分隔行」
// （GFM 语义：无分隔行不成表），不满足就落到兜底分支；而兜底分支的延续条件里恰好
// 带着 `!lines[i].trim().startsWith("|")` ⇒ 用 `while` 起手时**首轮即判定失败**。
//
// ## 实测事故（2026-09-12）
// `docs/kb/00-INDEX.md` 第 193 行一个空行把 KB-ENG 表截断，其后 194–198 行成了
// **无分隔行的表格块**；而该文件正是知识库面板**默认打开**的文档 ⇒ 用户进
// 「交易智能体 → 知识库」即整页卡住不动。全仓同源触发点 4 份文档 / 共 49 行
// （`kb/00-INDEX.md` · `retro-and-gaps.md` · `api.md` · `external-data-source-survey-*`）。
//
// 修法两条，缺一不可：
//   ① **文档**改为合法 GFM 表格（源码与其意图一致——空行分组在任何严格渲染器下
//      同样会把表打碎，属全仓系统性书写问题，不只是本面板的兼容问题）；
//   ② **渲染器**兜底分支改 `do...while`（防御纵深：此后**任何**输入都不可能不推进）。
//
// ⚠️ **注入验证的已知形态限制**：把兜底分支改回 `while` 后，本组用例会**挂死**而
// 不是变红——死循环发生在同步渲染期，测试超时机制无法打断。此处刻意接受：
// 「挂死」比「静默通过」安全得多，且挂死点就是缺陷本身。取证记录见账本 §6.14。

const DOCS_DIR = path.resolve(process.cwd(), "../../docs");

/** 渲染一份 markdown 并返回容器（断言走容器查询，避免非空断言的类型体操） */
function renderDoc(content: string) {
  return render(<MarkdownView content={content} />).container;
}

afterEach(cleanup);

describe("MarkdownView · 兜底分支必须推进（死循环守卫）", () => {
  // 四条输入都是修复前**实测会挂死**的形态（合成对照见账本 §6.14）。
  const pathological: Array<[string, string]> = [
    ["空行后的孤立表格行", "正文\n\n| a | b |\n"],
    ["末行即表格行（无后继行）", "正文\n| a | b |"],
    ["无分隔行的表格块（连行）", "| a | b |\n| c | d |\n"],
    ["孤立表格行后接标题", "| a | b |\n\n# 标题\n"],
  ];

  it.each(pathological)("%s：渲染必须返回（不得死循环）", (_name, content) => {
    const c = renderDoc(content);
    // 不成表的行按 GFM 语义退化为普通文本 ⇒ **原样的管道字符必须还在**，
    // 不能因「解析不了」就把这一行静默吞掉。
    expect(c.textContent).toContain("| a | b |");
  });

  it("退化后的孤立表格行不得被渲染成表格（无分隔行 = 不成表）", () => {
    const c = renderDoc("正文\n\n| a | b |\n");
    expect(c.querySelector("table")).toBeNull();
    expect(c.textContent).toContain("正文");
  });
});

describe("MarkdownView · 正常语法不回归", () => {
  it("合法表格（表头 + 分隔行）渲染为 table，行列数正确", () => {
    const c = renderDoc("| 方法 | 路径 |\n|---|---|\n| GET | /a |\n| POST | /b |\n");
    expect(c.querySelector("table")).toBeTruthy();
    expect(c.querySelectorAll("table thead th")).toHaveLength(2);
    expect(c.querySelectorAll("table tbody tr")).toHaveLength(2);
  });

  it("表格后的空行 + 段落仍正确分段（空行不把表格无限拉长）", () => {
    const c = renderDoc("| a |\n|---|\n| 1 |\n\n这是段落\n");
    expect(c.querySelectorAll("table tbody tr")).toHaveLength(1);
    expect(c.textContent).toContain("这是段落");
  });

  it("围栏代码块 / 标题 / 列表 / 引用 / 分隔线仍各自成块", () => {
    const c = renderDoc("# 标题\n\n- 项目\n\n> 引用\n\n```\ncode\n```\n\n---\n");
    expect(c.textContent).toContain("标题");
    expect(c.querySelector("li")?.textContent).toBe("项目");
    expect(c.querySelector("blockquote")?.textContent).toContain("引用");
    expect(c.querySelector("pre")?.textContent).toContain("code");
    expect(c.querySelector("hr")).toBeTruthy();
  });
});

describe("MarkdownView · 真实知识库文档可渲染", () => {
  it("默认打开的总索引 kb/00-INDEX.md 必须渲染完成且含表格", () => {
    // 端到端守卫：合成输入守住**机制**，这一条守住**事故现场**（面板首屏即这份文件）。
    const content = fs.readFileSync(path.join(DOCS_DIR, "kb/00-INDEX.md"), "utf8");
    const c = renderDoc(content);
    expect(c.querySelectorAll("table").length).toBeGreaterThan(0);
    expect(c.textContent).toContain("KB-ENG-68");
  });

  it("docs/ 下全部 md 均可渲染完成", () => {
    // 修复前：其中 4 份会在本循环里死循环，**整个测试进程挂住**（不是单条失败）。
    // 当前 corpus 已达 90+ 份 / 2MB+；GitHub runner 曾实测 5.185s，超过 Vitest 默认 5s。
    // 这里的同步死循环本来就无法被 timer timeout 打断（见文件头说明），因此给 corpus
    // 扫描显式 15s 只消除规模型假红，不降低结构性死循环守卫强度。
    const files: string[] = [];
    const walk = (dir: string) => {
      for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
        const p = path.join(dir, e.name);
        if (e.isDirectory()) walk(p);
        else if (e.name.endsWith(".md")) files.push(p);
      }
    };
    walk(DOCS_DIR);

    expect(files.length).toBeGreaterThan(50);
    for (const f of files) {
      renderDoc(fs.readFileSync(f, "utf8"));
      cleanup();
    }
  }, 15_000);
});
