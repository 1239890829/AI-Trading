import {cleanup,fireEvent,render,screen,within} from "@testing-library/react";
import {afterEach,expect,it} from "vitest";
import type {ArticleBlock} from "@/lib/api";
import {ArticleBlocks} from "./article-blocks";

afterEach(cleanup);
it("preserves source order, negations, table rows and images across reading and complete modes", () => {
  const blocks:ArticleBlock[]=[{type:"p",text:"拟收购，尚未审计，不代表已经完成。"},{type:"table",header:true,rows:[["价格","状态"],["12.35","待审计"]],truncated_rows:true},{type:"img",src:"https://example.com/source-image.png"},{type:"p",text:"审批仍需通过，不能推断业务受益。"},{type:"p",text:"来源时间未知，未补填。"},{type:"p",text:"第六条关键条件：交易可能终止。"}];
  const view=render(<ArticleBlocks identity="first" blocks={blocks}/>);
  expect(within(screen.getByRole("tabpanel",{name:"第 1 部分"})).getByText(blocks[0].type==="p"?blocks[0].text:"")).toBeTruthy();
  expect(screen.queryByText("第六条关键条件：交易可能终止。")).toBeNull();
  fireEvent.click(screen.getByRole("tab",{name:"第 2 部分"}));
  expect(screen.getByText("第六条关键条件：交易可能终止。")).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"完整原文"}));
  expect(screen.getByRole("table").textContent).toContain("12.35待审计");
  expect(screen.getByText("表格过长已截断，完整内容见原文")).toBeTruthy();
  const image=document.querySelector("img")!;
  expect(image.getAttribute("src")).toBe("https://example.com/source-image.png");
  fireEvent.error(image);
  expect(screen.getByText("[配图未能加载，可到原文查看]")).toBeTruthy();
  expect(screen.getByLabelText("完整原文").textContent).toContain("审批仍需通过，不能推断业务受益。");
  view.rerender(<ArticleBlocks identity="second" blocks={[{type:"p",text:"第二篇，仅展示本篇原文。"}]}/>);
  expect(screen.queryByText("第六条关键条件：交易可能终止。")).toBeNull();
  expect(screen.getByText("第二篇，仅展示本篇原文。")).toBeTruthy();
});
