import {afterEach, describe, expect, it} from "vitest";
import {cleanup, render, screen} from "@testing-library/react";
import {SourceThemeIdentity} from "./source-theme-identity";

afterEach(cleanup);
const exact={code:"885517.TI",name:"机器人概念",source:"ths_official",basis:"exact_name"};

describe("来源原因与正式概念身份分开",()=>{
  it("仅精确原名、正式代码和来源依据同时成立才显示同花顺身份",()=>{
    render(<SourceThemeIdentity reason="机器人概念" exact={exact}/>);
    expect(screen.getByText("同花顺 · 885517.TI")).toBeTruthy();
    expect(screen.getByTitle("同花顺正式概念 · 机器人概念 · 885517.TI")).toBeTruthy();
    expect(screen.queryByText(/正式概念未映射/)).toBeNull();
  });

  it.each([
    {label:"未知来源",patch:{source:"unknown"}},
    {label:"人工来源",patch:{source:"manual"}},
    {label:"成分重合",patch:{basis:"member_overlap"}},
    {label:"非同花顺代码",patch:{code:"BK0001"}},
    {label:"缺失代码",patch:{code:""}},
    {label:"相近而非相同名字",patch:{name:"人形机器人"}},
  ])("$label不能升级为正式身份，也不改写原始原因",({patch})=>{
    render(<span>机器人概念<SourceThemeIdentity reason="机器人概念" exact={{...exact,...patch}}/></span>);
    expect(screen.getByText("来源原因 · 正式概念未映射")).toBeTruthy();
    expect(screen.queryByText(/^同花顺 ·/)).toBeNull();
    expect(screen.getByText("机器人概念",{exact:true})).toBeTruthy();
  });

  it("仅有重合候选时明确非身份归属，正式候选名码留在可追溯提示中",()=>{
    render(<SourceThemeIdentity reason="产业订单" related={[{code:"885517.TI",name:"机器人概念",hits:3},{code:"886069.TI",name:"人形机器人",hits:2}]}/>);
    expect(screen.getByText("来源原因 · 正式概念未映射")).toBeTruthy();
    const overlap=screen.getByText("2项成分重合，非身份归属");
    expect(overlap.title).toContain("机器人概念 885517.TI：3只共同成分");
    expect(overlap.title).toContain("人形机器人 886069.TI：2只共同成分");
    expect(screen.queryByText(/^同花顺 ·/)).toBeNull();
  });

  it("精确身份存在时重合项也不成为它的别名或额外身份",()=>{
    render(<SourceThemeIdentity reason="机器人概念" exact={exact} related={[{code:"886002.TI",name:"机器视觉",hits:4}]}/>);
    expect(screen.getByText("同花顺 · 885517.TI")).toBeTruthy();
    expect(screen.getByText("1项成分重合，非身份归属")).toBeTruthy();
    expect(screen.queryByText("同花顺 · 886002.TI")).toBeNull();
  });
});
