import {afterEach, describe, expect, it} from "vitest";
import {cleanup, render, screen} from "@testing-library/react";
import {CurrentThemeReference, SourceThemeIdentity} from "./source-theme-identity";

afterEach(cleanup);
const exact={code:"885517.TI",name:"机器人概念",source:"ths_official",basis:"exact_name",kind:"concept" as const,catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00"};

describe("来源原因与正式概念身份分开",()=>{
  it("仅精确原名、正式代码和来源依据同时成立才显示同花顺身份",()=>{
    render(<SourceThemeIdentity state="exact" reason="机器人概念" exact={exact}/>);
    expect(screen.getByText("同花顺 · 885517.TI")).toBeTruthy();
    expect(screen.getByTitle(/同花顺正式概念 · 机器人概念 · 885517.TI/)).toBeTruthy();
    expect(screen.queryByText(/映射未确认/)).toBeNull();
  });

  it.each([
    {label:"未知来源",patch:{source:"unknown"}},
    {label:"人工来源",patch:{source:"manual"}},
    {label:"成分重合",patch:{basis:"member_overlap"}},
    {label:"非同花顺代码",patch:{code:"BK0001"}},
    {label:"缺失代码",patch:{code:""}},
    {label:"相近而非相同名字",patch:{name:"人形机器人"}},
  ])("$label不能升级为正式身份，也不改写原始原因",({patch})=>{
    render(<span>机器人概念<SourceThemeIdentity state="exact" reason="机器人概念" exact={{...exact,...patch}}/></span>);
    expect(screen.getByText("原标签 · 映射未确认")).toBeTruthy();
    expect(screen.queryByText(/^同花顺 ·/)).toBeNull();
    expect(screen.getByText("机器人概念",{exact:true})).toBeTruthy();
  });

  it("仅有重合候选时明确非身份归属，正式候选名码留在可追溯提示中",()=>{
    render(<SourceThemeIdentity state="exact" reason="产业订单" related={[{code:"885517.TI",name:"机器人概念",hits:3},{code:"886069.TI",name:"人形机器人",hits:2}]}/>);
    expect(screen.getByText("原标签 · 映射未确认")).toBeTruthy();
    const overlap=screen.getByText("2项成分重合，非身份归属");
    expect(overlap.title).toContain("机器人概念 885517.TI：3只共同成分");
    expect(overlap.title).toContain("人形机器人 886069.TI：2只共同成分");
    expect(screen.queryByText(/^同花顺 ·/)).toBeNull();
  });

  it("精确身份存在时重合项也不成为它的别名或额外身份",()=>{
    render(<SourceThemeIdentity state="exact" reason="机器人概念" exact={exact} related={[{code:"886002.TI",name:"机器视觉",hits:4}]}/>);
    expect(screen.getByText("同花顺 · 885517.TI")).toBeTruthy();
    expect(screen.getByText("1项成分重合，非身份归属")).toBeTruthy();
    expect(screen.queryByText("同花顺 · 886002.TI")).toBeNull();
  });
});


describe("冻结正式目录凭据",()=>{
  it("行业保留来源原名并明确类型",()=>{
    render(<SourceThemeIdentity state="exact" reason="汽车零部件" exact={{...exact,name:"汽车零部件",code:"881126.TI",kind:"industry"}}/>);
    expect(screen.getByTitle(/同花顺正式行业 · 汽车零部件 · 881126.TI/)).toBeTruthy();
  });
  it.each([{catalog_version:""},{observed_at:"2026-10-27 12:00:00"},{kind:undefined},{name:"人形机器人"}])("缺少或矛盾凭据不认正式身份 %j",patch=>{
    render(<SourceThemeIdentity state="exact" reason="机器人概念" exact={{...exact,...patch}}/>);
    expect(screen.queryByText(/^同花顺 ·/)).toBeNull();
  });
  it("有候选但处于歧义状态不认正式身份",()=>{
    render(<SourceThemeIdentity state="ambiguous" reason="机器人概念" exact={exact}/>);
    expect(screen.queryByText(/^同花顺 ·/)).toBeNull();
    expect(screen.getByText("原标签 · 目录对应不唯一")).toBeTruthy();
  });
  it("历史原记录未保存映射时不借今日目录补齐",()=>{
    render(<SourceThemeIdentity reason="具身智能" historical/>);
    expect(screen.getByText("原标签 · 当时未记录映射")).toBeTruthy();
  });
});


describe("当前目录参考独立于原记录",()=>{
  const reference={scope:"current_directory_reference",catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:01+08:00",items:[{theme:"机器人概念",catalog_mapping_state:"exact",exact_catalog:exact}]};
  it("仅具名同源目录凭据可作为当前参考，明确非当时归属",()=>{
    render(<CurrentThemeReference reason="机器人概念" reference={reference}/>);
    expect(screen.getByText("当前目录同名参考，非当时归属 · 885517.TI")).toBeTruthy();
  });
  it.each([
    {...reference,scope:"original_report"},
    {...reference,catalog_version:"b".repeat(64)},
    {...reference,items:[...reference.items,...reference.items]},
    {...reference,items:[{...reference.items[0],theme:"人形机器人"}]},
  ])("错scope/版本/重复或异名参考不套给原标签 %#",reference=>{
    render(<CurrentThemeReference reason="机器人概念" reference={reference}/>);
    expect(screen.queryByText(/当前目录同名参考/)).toBeNull();
  });
});


it("当前目录确认无对应时只说明缺口，不编正式代码",()=>{
  render(<CurrentThemeReference reason="具身智能" reference={{scope:"current_directory_reference",catalog_version:"a".repeat(64),observed_at:"2026-10-27T12:00:00+08:00",items:[{theme:"具身智能",catalog_mapping_state:"unmapped",exact_catalog:null}]}}/>);
  expect(screen.getByText("当前目录无同名对应")).toBeTruthy();
  expect(screen.queryByText(/\.TI/)).toBeNull();
});


it("当前目录失败明确不可用，不以空映射冒充没有对应",()=>{
  render(<CurrentThemeReference reason="具身智能" reference={{scope:"current_directory_reference",catalog_version:null,observed_at:null,items:[{theme:"具身智能",catalog_mapping_state:"unavailable",exact_catalog:null}]}}/>);
  expect(screen.getByText("当前目录不可用")).toBeTruthy();
  expect(screen.queryByText("当前目录无同名对应")).toBeNull();
});
