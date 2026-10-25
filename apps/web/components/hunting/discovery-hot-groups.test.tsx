import {describe, expect, it} from "vitest";
import type {HotTheme} from "@/lib/api/themes";
import {groupHotThemes} from "./discovery-hot-groups";

const anchors=[{theme:"机器人概念",theme_code:"885517.TI"},{theme:"人形机器人",theme_code:"886069.TI"},{theme:"机器视觉",theme_code:"886002.TI"}];
const symbols=["600001","600002","600003","600004","600005","600006"];
// Identity anchors are formal source names/codes; samples and values are isolated test data.
function row(index:number, hotSymbols:string[], heat=100): HotTheme {
  const unique=[...new Set(hotSymbols)];
  return {...anchors[index],source:"ths_official",heat,heat_complete:true,hot_count:unique.length,hot_symbols:hotSymbols,
    member_count:100,member_as_of:null,concentration_pct:unique.length,best:null,basis:"隔离样本"};
}

describe("重合热股仅作展示收拢",()=>{
  it("达到75%可收拢，保留每个代码与原数值，不将重合人数/人气重复求和",()=>{
    const a=row(0,symbols.slice(0,3),300),b=row(1,symbols.slice(0,4),450);
    Object.freeze(a.hot_symbols);Object.freeze(b.hot_symbols);Object.freeze(a);Object.freeze(b);
    const input=Object.freeze([a,b]);
    const groups=groupHotThemes(input);
    expect(groups).toHaveLength(1);expect(groups[0].primary).toBe(a);expect(groups[0].related).toEqual([b]);
    expect(groups[0].primary.hot_count).toBe(3);expect(groups[0].primary.heat).toBe(300);
    expect(groups[0].related[0].hot_count).toBe(4);expect(groups[0].related[0].heat).toBe(450);
    expect([groups[0].primary,...groups[0].related].map(item=>[item.theme_code,item.theme])).toEqual(anchors.slice(0,2).map(item=>[item.theme_code,item.theme]));
  });

  it("只跟原anchor比较，不能经中间条目传递合并",()=>{
    const a=row(0,symbols.slice(0,4)),b=row(1,symbols.slice(0,5)),c=row(2,symbols.slice(1,5));
    // A/B=4/5，B/C=4/5，但A/C=3/5；C不得借B混入A组。
    const groups=groupHotThemes([a,b,c]);
    expect(groups.map(group=>group.primary.theme_code)).toEqual([a.theme_code,c.theme_code]);
    expect(groups[0].related).toEqual([b]);expect(groups[1].related).toEqual([]);
  });

  it("名字相近但样本不同不合并，低于75%或不足3只也不合并",()=>{
    const a=row(0,symbols.slice(0,3)),b=row(1,symbols.slice(3,6));
    expect(groupHotThemes([a,b])).toHaveLength(2);
    expect(groupHotThemes([a,row(1,symbols.slice(0,5))])).toHaveLength(2);
    expect(groupHotThemes([row(0,symbols.slice(0,2)),row(1,symbols.slice(0,2))])).toHaveLength(2);
  });

  it("空或未知热股样本保持独立，未知集中度不虚构成确定的热股集合",()=>{
    const a=row(0,symbols.slice(0,3));
    const unknown={...row(1,[]),member_count:null,concentration_pct:null};
    const otherUnknown={...row(2,[]),member_count:null,concentration_pct:null};
    const groups=groupHotThemes([a,unknown,otherUnknown]);
    expect(groups).toHaveLength(3);expect(groups.every(group=>group.related.length===0)).toBe(true);
    expect(groups[1].primary.concentration_pct).toBeNull();
  });

  it("重复出现的同股不放大相似度，关闭收拢时原条目顺序和对象完整恢复",()=>{
    const a=row(0,symbols.slice(0,3)),b=row(1,[symbols[0],symbols[0],symbols[1],symbols[2]]);
    const groups=groupHotThemes([a,b]);
    expect(groups).toHaveLength(1);expect(groups[0].related[0].hot_count).toBe(3);
    const expanded=groupHotThemes([a,b],false);
    expect(expanded).toEqual([{primary:a,related:[]},{primary:b,related:[]}]);
    expect(expanded[0].primary).toBe(a);expect(expanded[1].primary).toBe(b);
    expect(groupHotThemes([])).toEqual([]);
  });
});
