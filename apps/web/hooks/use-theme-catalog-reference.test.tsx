import {StrictMode} from "react";
import {act,cleanup,render,renderHook,screen,waitFor} from "@testing-library/react";
import {afterEach,beforeEach,expect,it,vi} from "vitest";
import {useThemeCatalogReference} from "./use-theme-catalog-reference";
import {CurrentThemeReference} from "@/components/source-theme-identity";
import {getThemeCatalogCompletion,type ThemeCatalogCompletion} from "@/lib/api/themes";
import {clearDeviceSessionReads} from "@/lib/api/internal";
import {ApiError} from "@/lib/api/client";
import type {CurrentThemeCatalogReference} from "@/types/market";

vi.mock("@/lib/api/themes",async()=>({...await vi.importActual<typeof import("@/lib/api/themes")>("@/lib/api/themes"),getThemeCatalogCompletion:vi.fn()}));
const version="a".repeat(64),clock="2026-10-28T00:00:00+00:00";
const key=(n:number)=>n.toString(16).padStart(32,"0");
const pending=(token:string|undefined,names=["汽车零部件","具身智能"]):CurrentThemeCatalogReference=>({scope:"current_directory_reference",catalog_version:null,observed_at:null,completion_key:token,items:names.map(theme=>({theme,exact_catalog:null,catalog_mapping_state:"unavailable",catalog_mapping_context:{reason:"catalog_read_pending",completion_key:token}}))});
const completed=(token:string):ThemeCatalogCompletion=>({scope:"current_directory_resolution",completion_key:token,state:"available",catalog_version:version,observed_at:clock,identities:{汽车零部件:{code:"881126.TI",name:"汽车零部件",source:"ths_official",basis:"exact_name",kind:"industry",catalog_version:version,observed_at:clock}},ambiguous:[],reason:null});
function deferred(){let resolve!:(value:ThemeCatalogCompletion)=>void;let reject!:(error:unknown)=>void;const promise=new Promise<ThemeCatalogCompletion>((yes,no)=>{resolve=yes;reject=no;});return {promise,resolve,reject};}
function visibility(state:"visible"|"hidden"){Object.defineProperty(document,"visibilityState",{configurable:true,value:state});document.dispatchEvent(new Event("visibilitychange"));}
function Reader({reference,scope="same",active=true}:{reference:CurrentThemeCatalogReference;scope?:string;active?:boolean}){
  const result=useThemeCatalogReference(reference,scope,active);
  return <div>{reference.items.map(item=><CurrentThemeReference key={item.theme} reason={item.theme} reference={result}/>)}</div>;
}
beforeEach(()=>{clearDeviceSessionReads();vi.mocked(getThemeCatalogCompletion).mockReset();visibility("visible");});
afterEach(cleanup);

it("StrictMode 多读者只等待一次，完成后自动投影同包标签且不改原记录",async()=>{
  const source=pending(key(1)),before=JSON.stringify(source),work=deferred();
  vi.mocked(getThemeCatalogCompletion).mockReturnValue(work.promise);
  const view=render(<StrictMode><Reader reference={source}/><Reader reference={source}/></StrictMode>);
  expect(getThemeCatalogCompletion).toHaveBeenCalledTimes(1);
  expect(screen.getAllByText("目录核对中")).toHaveLength(4);
  await act(async()=>work.resolve(completed(key(1))));
  expect(screen.getAllByText("当前目录同名参考，非当时归属 · 881126.TI")).toHaveLength(2);
  expect(screen.getAllByText("当前目录无同名对应")).toHaveLength(2);
  expect(JSON.stringify(source)).toBe(before);
  view.unmount();
  render(<Reader reference={source}/>);
  expect(screen.getByText("当前目录同名参考，非当时归属 · 881126.TI")).toBeTruthy();
  expect(getThemeCatalogCompletion).toHaveBeenCalledTimes(1);
});

it("隐藏或停用的读者不发起等待，恢复只消费当前 scope 并清理终态 key",async()=>{
  const source=pending(key(2)),work=deferred();vi.mocked(getThemeCatalogCompletion).mockReturnValue(work.promise);
  visibility("hidden");
  const view=renderHook(({active})=>useThemeCatalogReference(source,"scope",active),{initialProps:{active:false}});
  expect(getThemeCatalogCompletion).not.toHaveBeenCalled();
  view.rerender({active:true});expect(getThemeCatalogCompletion).not.toHaveBeenCalled();
  act(()=>visibility("visible"));expect(getThemeCatalogCompletion).toHaveBeenCalledTimes(1);
  await act(async()=>work.resolve(completed(key(2))));
  expect(view.result.current?.items[0].catalog_mapping_state).toBe("exact");
  expect(view.result.current?.completion_key).toBeUndefined();
  expect(view.result.current?.items[0].catalog_mapping_context?.completion_key).toBeUndefined();
});

it("日期或 key 切换后旧完成包不串入新 scope，同 key 也只投影当前标签",async()=>{
  const first=deferred(),second=deferred();vi.mocked(getThemeCatalogCompletion).mockImplementation(token=>token===key(3)?first.promise:second.promise);
  const view=renderHook(({reference,scope})=>useThemeCatalogReference(reference,scope),{initialProps:{reference:pending(key(3)),scope:"day-A:v1"}});
  view.rerender({reference:pending(key(4),["新标签"]),scope:"day-B:v2"});
  await act(async()=>first.resolve(completed(key(3))));
  expect(view.result.current?.items[0].theme).toBe("新标签");
  expect(view.result.current?.items[0].catalog_mapping_state).toBe("unavailable");
  await act(async()=>second.resolve(completed(key(4))));
  expect(view.result.current?.items[0].catalog_mapping_state).toBe("unmapped");
  view.rerender({reference:pending(key(4),["汽车零部件"]),scope:"day-C:v3"});
  expect(view.result.current?.items[0].exact_catalog?.code).toBe("881126.TI");
  expect(getThemeCatalogCompletion).toHaveBeenCalledTimes(2);
});

it("配对会话更换后不接收旧等待结果",async()=>{
  const first=deferred(),second=deferred(),source=pending(key(5));
  vi.mocked(getThemeCatalogCompletion).mockReturnValueOnce(first.promise).mockReturnValueOnce(second.promise);
  const view=renderHook(({scope})=>useThemeCatalogReference(source,scope),{initialProps:{scope:"old-session"}});
  clearDeviceSessionReads();view.rerender({scope:"new-session"});
  await act(async()=>first.resolve(completed(key(5))));
  expect(view.result.current?.items[0].catalog_mapping_state).toBe("unavailable");
  await act(async()=>second.resolve({...completed(key(5)),identities:{}}));
  expect(view.result.current?.items[0].catalog_mapping_state).toBe("unmapped");
  expect(getThemeCatalogCompletion).toHaveBeenCalledTimes(2);
});

it.each([undefined,"broken"])("pending 缺失或损坏 key %s 终态可见，不猜新请求",token=>{
  const {result}=renderHook(()=>useThemeCatalogReference(pending(token),"damaged"));
  expect(getThemeCatalogCompletion).not.toHaveBeenCalled();
  expect(result.current?.items[0].catalog_mapping_context?.reason).toBe(token==null?"completion_key_missing":"completion_key_invalid");
  expect(result.current?.completion_key).toBeUndefined();
});

it.each(["source_failed","expired"])("失败 %s 只等待一次并给可恢复终态",async failure=>{
  const source=pending(key(6));
  if(failure==="expired")vi.mocked(getThemeCatalogCompletion).mockRejectedValue(new ApiError(410,"expired","not_found"));
  else vi.mocked(getThemeCatalogCompletion).mockResolvedValue({...completed(key(6)),state:"unavailable",catalog_version:null,observed_at:null,identities:{},reason:"completion_source_failed"});
  const view=render(<Reader reference={source}/>);
  await waitFor(()=>expect(screen.getAllByText("目录核对未完成，请重读")).toHaveLength(2));
  view.rerender(<Reader reference={source} scope="same-return"/>);
  expect(getThemeCatalogCompletion).toHaveBeenCalledTimes(1);
});

it("原始标签只读 own identity，prototype 名不借继承值，未分类不升级",async()=>{
  const source=pending(key(7),["toString","__proto__","汽车零部件"]);source.items[2].catalog_mapping_state="unclassified";
  vi.mocked(getThemeCatalogCompletion).mockResolvedValue(completed(key(7)));
  const {result}=renderHook(()=>useThemeCatalogReference(source,"prototype"));
  await waitFor(()=>expect(result.current?.items[0].catalog_mapping_state).toBe("unmapped"));
  expect(result.current?.items[1].catalog_mapping_state).toBe("unmapped");
  expect(result.current?.items[2].catalog_mapping_state).toBe("unclassified");
  expect(result.current?.items.every(item=>item.exact_catalog===null)).toBe(true);
});

it("八个在途任务满载时，第九 token 多读者卸载一个不丢容量终态也不发第九请求",async()=>{
  vi.mocked(getThemeCatalogCompletion).mockReturnValue(new Promise(()=>{}));
  const pinned=render(<>{Array.from({length:8},(_,i)=><Reader key={i} reference={pending(key(i+100))}/>)}</>);
  const source=pending(key(999));
  const readers=render(<><Reader key="one" reference={source} scope="one"/><Reader key="two" reference={source} scope="two"/><Reader key="three" reference={source} scope="three"/></>);
  await waitFor(()=>expect(screen.getAllByText("目录核对未完成，请重读")).toHaveLength(6));
  readers.rerender(<><Reader key="two" reference={source} scope="two"/><Reader key="three" reference={source} scope="three"/></>);
  expect(screen.getAllByText("目录核对未完成，请重读")).toHaveLength(4);
  expect(getThemeCatalogCompletion).toHaveBeenCalledTimes(8);
  pinned.unmount();
});
