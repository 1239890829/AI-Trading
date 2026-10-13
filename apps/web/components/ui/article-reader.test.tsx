import {cleanup,fireEvent,render,screen,within} from "@testing-library/react";
import {afterEach,expect,it} from "vitest";
import type {ArticleBlock} from "@/lib/api";
import {ArticleBlocks} from "./article-blocks";
import {ArticleReader} from "./article-reader";
import {ExplanationMap} from "./explanation-map";

afterEach(cleanup);
it("makes the source excerpt conditions real image and table readable without opening a menu",()=>{
  const blocks:ArticleBlock[]=[{type:"p",text:"拟收购，尚未审计，不代表已经完成。"},{type:"table",header:true,rows:[["价格","状态"],["12.35","待审计"]],truncated_rows:true},{type:"img",src:"https://example.com/source-image.png"},{type:"p",text:"审批仍需通过，不能推断业务受益。"},{type:"p",text:"来源时间未知，未补填。"},{type:"p",text:"第六条关键条件：交易可能终止。"}];
  const view=render(<ArticleBlocks identity="first" blocks={blocks}/>);
  expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.getByLabelText("原文摘录").querySelector("blockquote")?.textContent).toBe("拟收购，尚未审计，不代表已经完成。");
  expect(screen.getByLabelText("原文配图").querySelector("img")?.getAttribute("src")).toBe("https://example.com/source-image.png");
  expect(screen.getByRole("table").textContent).toContain("12.35待审计");
  expect(screen.getByRole("button",{name:"不代表已经完成。"})).toBeTruthy();
  expect(screen.getByRole("button",{name:"第六条关键条件：交易可能终止。"})).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"完整原文"}));
  const full=screen.getByLabelText("完整原文");
  expect(full.querySelectorAll("p")[0].textContent).toBe("拟收购，尚未审计，不代表已经完成。");
  expect(full.querySelector("table")?.textContent).toContain("12.35待审计");
  expect(screen.getByText("表格过长已截断，完整内容见原文")).toBeTruthy();
  const image=full.querySelector("img")!;fireEvent.error(image);
  expect(screen.getByText("[配图未能加载，可到原文查看]")).toBeTruthy();
  expect(full.textContent).toContain("审批仍需通过，不能推断业务受益。");
  view.rerender(<ArticleBlocks identity="second" blocks={[{type:"p",text:"第二篇，仅展示本篇原文。"}]}/>);
  expect(screen.queryByLabelText("完整原文")).toBeNull();expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.queryByText("第六条关键条件：交易可能终止。")).toBeNull();
  expect(screen.getByLabelText("原文摘录").querySelector("blockquote")?.textContent).toBe("第二篇，仅展示本篇原文。");
});
it("shows every limiting amount with complete context even with only one paragraph",()=>{
  const text="按最高30%补贴，年度补贴上限200万元，累计补贴比例不突破30%，年采购费用需达到25万元及以上。";
  render(<ArticleBlocks identity="policy" blocks={[{type:"p",text}]}/>);
  expect(screen.queryByRole("tab")).toBeNull();
  const required=["按最高30%补贴，","年度补贴上限200万元，","累计补贴比例不突破30%，","年采购费用需达到25万元及以上。"];
  required.forEach(quote=>expect(screen.getByRole("button",{name:quote})).toBeTruthy());
  fireEvent.click(screen.getByRole("button",{name:required[2]}));
  expect(screen.getByRole("region",{name:"原文线索对应原句"}).querySelector("blockquote")?.textContent).toBe(text);
  fireEvent.click(screen.getByRole("button",{name:"完整原文"}));expect(screen.getByLabelText("完整原文").textContent).toBe(text);
});
it("does not fabricate source media or an empty material directory when absent",()=>{
  render(<ArticleBlocks identity="no-media" blocks={[{type:"p",text:"市场消息待核，未给出正式依据。"}]}/>);
  expect(document.querySelector("img")).toBeNull();expect(screen.queryByLabelText("原文配图")).toBeNull();
  expect(screen.queryByLabelText("原文表格 1")).toBeNull();expect(screen.queryByRole("tab")).toBeNull();
  expect(screen.getByText("按原文内容整理，未自动摘要、归因或核实。")).toBeTruthy();
});
it("keeps stacking and platform registration conditions visible as untouched source cards",()=>{
  const text="市县已有算力相关补贴可按规定叠加享受。年采购费用需达到25万元及以上，并完成省级算力平台注册；必须核对申请资格。";
  render(<ArticleBlocks identity="policy-conditions" blocks={[{type:"p",text}]}/>);
  expect(screen.getByRole("button",{name:"市县已有算力相关补贴可按规定叠加享受。"})).toBeTruthy();
  const registration=screen.getByRole("button",{name:"并完成省级算力平台注册；"});
  expect(registration.getAttribute("aria-expanded")).toBe("false");
  expect(screen.getByRole("button",{name:"年采购费用需达到25万元及以上，"})).toBeTruthy();
  expect(screen.getByRole("button",{name:"必须核对申请资格。"})).toBeTruthy();
  fireEvent.click(registration);
  expect(screen.getByRole("region",{name:"原文线索对应原句"}).querySelector("blockquote")?.textContent).toBe("年采购费用需达到25万元及以上，并完成省级算力平台注册；必须核对申请资格。");
});
it("needs only one click to read complete blocks inside an already-open structured evidence node",()=>{
  const text="拟收购，尚未审计，不代表已经完成。";
  render(<ExplanationMap label="外层资料" steps={[{id:"source",label:"原始报道",content:<ArticleBlocks identity="inside" blocks={[{type:"p",text}]}/>} ]}/>);
  fireEvent.click(screen.getByRole("tab",{name:"原始报道"}));
  expect(screen.getAllByRole("tab")).toHaveLength(1);expect(within(screen.getByRole("tabpanel")).getByText(text)).toBeTruthy();
  expect(screen.queryByRole("button",{name:"完整原文"})).toBeNull();
});
it("previews a large original table and expands all acquired rows with one click",()=>{
  const rows=[["项目","额度"],["甲","30%"],["乙","200万元"],["丙","25万元"],["丁","待核"]];
  render(<ArticleBlocks identity="table" blocks={[{type:"table",header:true,rows,truncated_rows:true}]}/>);
  expect(screen.getByRole("table").textContent).toContain("甲30%乙200万元");
  expect(screen.getByText("表格过长已截断，完整内容见原文")).toBeTruthy();
  expect(screen.queryByText("25万元")).toBeNull();
  fireEvent.click(screen.getByRole("button",{name:"展开表格 1"}));
  expect(screen.getByRole("table").textContent).toContain("丙25万元丁待核");
});
it("skips only the explicitly supplied identical title and quotes the first body sentence exactly",()=>{
  const title="原报道标题";
  const body="  正文讲述供应链进展。随后补充原有背景。";
  const blocks:ArticleBlock[]=[{type:"p",text:title},{type:"p",text:body}];
  render(<ArticleReader identity="title" title={title} blocks={blocks} renderBlock={block=>block.type==="p"?<p>{block.text}</p>:null}/>);
  expect(screen.getByLabelText("原文摘录").querySelector("blockquote")?.textContent).toBe("正文讲述供应链进展。");
  fireEvent.click(screen.getByRole("button",{name:"完整原文"}));
  expect(screen.getByLabelText("完整原文").textContent).toBe(title+body);
});
it("uses a readable source paragraph outline for long prose without numeric or limiting clues",()=>{
  const blocks:ArticleBlock[]=[{type:"p",text:"团队介绍材料研发历程。随后描述现场情况。"},{type:"p",text:"厂区展示设备装配过程。接着说明工艺环节。"},{type:"p",text:"客户交流产品体验。最后描述参访安排。"}];
  render(<ArticleBlocks identity="plain" blocks={blocks}/>);
  expect(screen.queryByRole("tab")).toBeNull();expect(screen.queryByLabelText("原文线索")).toBeNull();
  expect(screen.getByLabelText("原文摘录").querySelector("blockquote")?.textContent).toBe("团队介绍材料研发历程。");
  expect(within(screen.getByLabelText("原文大纲")).getAllByRole("listitem").map(item=>item.textContent)).toEqual(["厂区展示设备装配过程。","客户交流产品体验。"]);
  fireEvent.click(screen.getByRole("button",{name:"完整原文"}));
  expect(screen.getByLabelText("完整原文").textContent).toBe(blocks.map(block=>block.type==="p"?block.text:"").join(""));
});
it("keeps numeric column formatting consistent between default preview and all original rows",()=>{
  const rows=[["名称","价格","混合字段"],["甲","12.36","1"],["乙","-2.80%","2"],["丙","10.25","待核"],["丁","未知","缺失"]];
  render(<ArticleBlocks identity="numeric" blocks={[{type:"table",header:true,rows}]}/>);
  const preview=screen.getByRole("table");
  const first=preview.querySelector("tbody tr")!.children;
  expect((first[1] as HTMLElement).className).toContain("font-mono");
  expect((first[1] as HTMLElement).className).toContain("text-right");
  expect((first[0] as HTMLElement).className).toContain("text-left");
  // Classification uses all acquired rows, not just the two visible numeric cells.
  expect((first[2] as HTMLElement).className).not.toContain("font-mono");
  fireEvent.click(screen.getByRole("button",{name:"完整原文"}));
  const complete=screen.getByRole("table").querySelector("tbody tr")!.children;
  expect((complete[1] as HTMLElement).className).toContain("font-mono");
  expect((complete[1] as HTMLElement).className).toContain("text-right");
  expect((complete[2] as HTMLElement).className).not.toContain("font-mono");
});
it("suppresses only an already shown identical short source excerpt and retains every original block and condition",()=>{
  const title="政策事项";
  const source=`【${title}】补贴比例不超过30%，尚需资格审核。`;
  render(<ArticleBlocks identity="same" title={title} alreadyShownExcerpt={source} blocks={[{type:"p",text:source},{type:"p",text:"没有保证所有主体均适用。"}]}/>);
  expect(screen.queryByLabelText("原文摘录")).toBeNull();
  expect(screen.getByRole("button",{name:`【${title}】补贴比例不超过30%，`})).toBeTruthy();
  expect(screen.getByRole("button",{name:"尚需资格审核。"})).toBeTruthy();
  expect(screen.getByRole("button",{name:"没有保证所有主体均适用。"})).toBeTruthy();
  fireEvent.click(screen.getByRole("button",{name:"完整原文"}));
  expect(screen.getByLabelText("完整原文").textContent).toBe(source+"没有保证所有主体均适用。");
});
it("never suppresses a mismatching or long summary, a negation difference or an undeclared title prefix",()=>{
  const title="政策事项";
  const source=`【${title}】公司尚未获得批准，不构成生效。`;
  const view=render(<ArticleBlocks identity="mismatch" title={title} alreadyShownExcerpt="公司已经获得批准，不构成生效。" blocks={[{type:"p",text:source}]}/>);
  expect(screen.getByLabelText("原文摘录").textContent).toContain("尚未获得批准");
  const long="公司尚未获得批准，不构成生效。".repeat(12);
  view.rerender(<ArticleBlocks identity="long" title={title} alreadyShownExcerpt={long} blocks={[{type:"p",text:long}]}/>);
  expect(screen.getByLabelText("原文摘录")).toBeTruthy();
  view.rerender(<ArticleBlocks identity="undeclared" alreadyShownExcerpt="公司尚未获得批准，不构成生效。" blocks={[{type:"p",text:source}]}/>);
  expect(screen.getByLabelText("原文摘录")).toBeTruthy();
  view.rerender(<ArticleBlocks identity="bare-word" title="AI" alreadyShownExcerpt="需求尚未核实。" blocks={[{type:"p",text:"AI需求尚未核实。"}]}/>);
  expect(screen.getByLabelText("原文摘录").textContent).toContain("AI需求尚未核实");
});
