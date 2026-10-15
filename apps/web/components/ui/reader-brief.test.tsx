import {afterEach,expect,it,vi} from "vitest";
import {cleanup,fireEvent,render,screen} from "@testing-library/react";
import {ReaderBrief,ResearchDisclosure} from "./reader-brief";
import {readerCautions,readerExcerpt} from "@/lib/reader-copy";
afterEach(cleanup);
it("keeps a long late qualification out of the default prose, but flags it and preserves the exact record on one action",()=>{
 const raw="拟收购公告背景。".repeat(40)+"没有借壳计划；金额不超过12.36亿元；尚未完成审计，不代表公司受益。";
 render(<><ReaderBrief label="速读" headline={readerExcerpt(raw)} cautions={readerCautions([raw])}/><ResearchDisclosure identity="a" label="依据"><p>{raw}</p></ResearchDisclosure></>);
 expect(screen.getByLabelText("速读").textContent!.length).toBeLessThan(140);
 expect(screen.getByText("仍有待确认事项")).toBeTruthy();expect(screen.getByText("注意适用范围与限制")).toBeTruthy();
 expect(screen.queryByText(raw)).toBeNull();fireEvent.click(screen.getByRole("button",{name:"依据"}));expect(screen.getByText(raw).textContent).toBe(raw);
});
it("does not let evidence clicks navigate the containing stock card, and retires it on identity change",()=>{
 const navigate=vi.fn();const view=render(<div onClick={navigate}><ResearchDisclosure identity="a" label="依据"><p>对象A完整记录</p></ResearchDisclosure></div>);
 const toggle=screen.getByRole("button",{name:"依据"});fireEvent.click(toggle);
 expect(navigate).not.toHaveBeenCalled();expect(toggle.getAttribute("aria-expanded")).toBe("true");
 view.rerender(<div onClick={navigate}><ResearchDisclosure identity="b" label="依据"><p>对象B完整记录</p></ResearchDisclosure></div>);
 expect(screen.getByRole("button",{name:"依据"}).getAttribute("aria-expanded")).toBe("false");expect(screen.queryByText("对象A完整记录")).toBeNull();expect(screen.queryByText("对象B完整记录")).toBeNull();
});
it("does not manufacture safety or benefit when no factual condition is supplied",()=>{
 expect(readerExcerpt(null)).toBe("");expect(readerCautions([])).toEqual([]);
 expect(readerExcerpt("公司拟收购，但尚未获批")).toContain("拟收购");expect(readerExcerpt("公司拟收购，但尚未获批")).toContain("尚未获批");
});
