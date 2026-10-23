import {cleanup, fireEvent, render, screen, within} from "@testing-library/react";
import {afterEach, describe, expect, it} from "vitest";
import {SelectionPriceComparison, type SelectionPriceItem} from "./selection-price-comparison";

afterEach(cleanup);
function item(): SelectionPriceItem {
  return {symbol:"000001",name:"样本甲",origin:"picks",price:12.5,
    selectionEntry:{state:"recorded",selected_at:"2026-10-23T09:26:00+08:00",reference_price:12.5,reference_change_pct:0,
      quote_as_of:"2026-10-23T09:25:00+08:00",source:"daily_generation",source_version:"original-v1",semantics:"observation_only_not_fill"},
    execution:{contract_version:"1",decision_id:"decision-1",decision_version:"1",strategy_version:"1",feature_version:"1",
      reference_entry:{price:12.5,as_of:"2026-10-23T09:25:00+08:00",source:"fixture",semantics:"observation_only_not_fill"},
      executable_snapshot:{state:"ready",price:13.75,change_pct:10,source:"tencent",as_of:"2026-10-23T10:00:00+08:00"},gate_decision:"rejected"}};
}

describe("two saved selection-price observations", () => {
  it("compares complete observations, distinguishes observation from fill, and exposes each endpoint", () => {
    render(<SelectionPriceComparison item={item()}/>);
    expect(screen.getByLabelText("价格观察差值").textContent).toContain("+10.00%");
    expect(screen.getByText("观察记录，非成交收益")).toBeTruthy();
    fireEvent.focus(screen.getByRole("button",{name:"价格刻度：执行复核"}));
    const context=screen.getByText("2026-10-23 10:00:00").parentElement!;
    expect(context.textContent).toContain("腾讯");
    fireEvent.click(screen.getByRole("button",{name:"价格刻度：加入记录"}));
    expect(screen.getByText("版本 original-v1")).toBeTruthy();
  });

  it("shows an intraday list price without borrowing metric clocks or claiming a trustworthy return", () => {
    const value=item();value.origin="intraday";value.price=14;
    render(<SelectionPriceComparison item={value}/>);
    expect(screen.getByRole("button",{name:"核对本次名单价"}).textContent).toContain("¥14.00");
    expect(screen.getByLabelText("价格观察差值").textContent).toContain("名单价缺报价时点");
    expect(screen.queryByLabelText("两次真实价格记录的时序对照")).toBeNull();
    fireEvent.click(screen.getByRole("button",{name:"核对本次名单价"}));
    expect(screen.getByText("名单展示，不据此验证报价新鲜")).toBeTruthy();
  });

  it("keeps failed or stale observations visible without publishing a comparison", () => {
    const value=item();const {rerender}=render(<SelectionPriceComparison item={value} readError/>);
    expect(screen.getByLabelText("价格观察差值").textContent).toContain("本次读取失败");
    expect(screen.getByRole("button",{name:"核对执行复核记录"}).textContent).toContain("¥13.75");
    value.execution!.executable_snapshot.state="stale";
    rerender(<SelectionPriceComparison item={value}/>);
    expect(screen.queryByLabelText("两次真实价格记录的时序对照")).toBeNull();
  });

  it("does not repair incomplete, impossible or backward timestamps into comparable observations", () => {
    const {rerender}=render(<SelectionPriceComparison item={item()}/>);
    for(const at of [null,"2026-02-30T10:00:00+08:00","2026-10-23T10:00:00","2026-10-22T10:00:00+08:00","2026-10-23T09:25:30+08:00"]){
      const value=item();value.execution!.executable_snapshot.as_of=at;
      rerender(<SelectionPriceComparison item={value}/>);
      expect(screen.queryByLabelText("两次真实价格记录的时序对照")).toBeNull();
    }
  });

  it("requires the recorded entry identity and finite positive prices rather than zero-fill", () => {
    const {rerender}=render(<SelectionPriceComparison item={item()}/>);
    for(const price of [null,0,NaN,Infinity,-1]){
      const value=item();value.selectionEntry!.reference_price=price;
      rerender(<SelectionPriceComparison item={value}/>);
      expect(within(screen.getByRole("button",{name:"核对加入参考价"})).getByText("未提供")).toBeTruthy();
      expect(screen.queryByLabelText("两次真实价格记录的时序对照")).toBeNull();
    }
    const missing=item();missing.selectionEntry!.source_version=null;
    rerender(<SelectionPriceComparison item={missing}/>);
    expect(screen.queryByLabelText("两次真实价格记录的时序对照")).toBeNull();
    const overflow=item();overflow.selectionEntry!.reference_price=Number.MIN_VALUE;
    rerender(<SelectionPriceComparison item={overflow}/>);
    expect(screen.queryByLabelText("两次真实价格记录的时序对照")).toBeNull();
  });

  it("keeps the chosen endpoint through quote refresh, and resets it on stock or entry-version change", () => {
    const value=item();const {rerender}=render(<SelectionPriceComparison item={value}/>);
    fireEvent.focus(screen.getByRole("button",{name:"核对执行复核记录"}));
    value.execution!.executable_snapshot.price=13.8;
    rerender(<SelectionPriceComparison item={value}/>);
    expect(screen.getByRole("button",{name:"核对执行复核记录"}).getAttribute("aria-pressed")).toBe("true");
    value.symbol="000002";
    rerender(<SelectionPriceComparison item={value}/>);
    expect(screen.getByRole("button",{name:"核对加入参考价"}).getAttribute("aria-pressed")).toBe("true");
    fireEvent.click(screen.getByRole("button",{name:"核对执行复核记录"}));
    value.selectionEntry!.source_version="original-v2";
    rerender(<SelectionPriceComparison item={value}/>);
    expect(screen.getByRole("button",{name:"核对加入参考价"}).getAttribute("aria-pressed")).toBe("true");
  });
});
