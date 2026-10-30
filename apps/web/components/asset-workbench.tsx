"use client";

import Link from "next/link";
import {useEffect, useState} from "react";
import {useRouter, useSearchParams} from "next/navigation";
import {WorkbenchFrame} from "@/components/workbench-frame";
import {FundDetail} from "@/components/fund-detail";
import {Skeleton} from "@/components/ui/loading";
import {useResource} from "@/hooks/use-resource";
import {getAssetCapabilities, getAssetRecords, getFundSnapshot} from "@/lib/api/assets";
import {ASSET_RECORDS_EVENT, assetWorkbenchUrl, fundIdentityMatches, validFundSymbol} from "@/lib/asset-identity";

export function AssetWorkbench({asset}: {asset: "fund" | "us_equity"}) {
  const params = useSearchParams();
  const router = useRouter();
  const mode = params.get("mode") === "positions" ? "positions" : "watch";
  const sourceSymbol = params.get("symbol");
  const [range, setRange] = useState<"month" | "year">("year");
  const [mobileView, setMobileView] = useState<"list" | "chart">(sourceSymbol ? "chart" : "list");
  const [appliedSymbol, setAppliedSymbol] = useState(sourceSymbol);
  if (sourceSymbol !== appliedSymbol) {
    setAppliedSymbol(sourceSymbol);
    if (sourceSymbol) setMobileView("chart");
  }
  const capability = useResource(signal => getAssetCapabilities({signal}), {key: "asset-capabilities", abortable: true, refreshOnVisible: false});
  const available = capability.data?.items.find(item => item.asset_type === asset);
  const records = useResource(signal => getAssetRecords({signal}), {key: "fund-records", enabled: asset === "fund", abortable: true, refreshOnVisible: false});
  useEffect(() => {window.addEventListener(ASSET_RECORDS_EVENT, records.refresh); return () => window.removeEventListener(ASSET_RECORDS_EVENT, records.refresh);}, [records.refresh]);
  const valid = asset === "fund" && validFundSymbol(sourceSymbol);
  const snapshot = useResource(signal => getFundSnapshot(sourceSymbol!, range, {signal}), {
    key: `${asset}:${sourceSymbol ?? ""}:${range}`, enabled: valid && available?.state === "available", abortable: true, refreshOnVisible: false,
  });
  const identityMismatch = snapshot.data && (!fundIdentityMatches(snapshot.data.instrument) || snapshot.data.instrument.source_symbol !== sourceSymbol);
  const data = identityMismatch ? undefined : snapshot.data;
  const selectedRecord = records.data?.items.find(item => item.instrument_id === data?.instrument.instrument_id
    && item.source_symbol === data.instrument.source_symbol && item.currency === data.instrument.currency);
  const rows = records.data?.items.filter(item => mode === "positions" ? item.units != null && item.units > 0 : item.is_watched) ?? [];
  function select(symbol: string) {setMobileView("chart"); router.replace(assetWorkbenchUrl(asset, symbol, mode), {scroll: false});}
  return <WorkbenchFrame asset={asset} actions={asset === "fund" ? <nav aria-label="个人对象" className="bc-object-switch"><Link href={assetWorkbenchUrl(asset, sourceSymbol ?? undefined, "watch")} aria-current={mode === "watch" ? "page" : undefined}>自选跟踪</Link><Link href={assetWorkbenchUrl(asset, sourceSymbol ?? undefined, "positions")} aria-current={mode === "positions" ? "page" : undefined}>持有记录</Link></nav> : <span className="asset-note">自选查看 · 不参与 A 股选股</span>}>
    <nav className="bc-mobile-views" aria-label="工作台内容"><button aria-pressed={mobileView === "list"} onClick={() => setMobileView("list")}>资产列表</button><button aria-pressed={mobileView === "chart"} onClick={() => setMobileView("chart")}>详情</button></nav>
    <div className="bc-workbench-layout" data-mobile-view={mobileView}>
      <aside className="bc-workbench-sidebar" aria-label="资产列表">
        <section className="bc-attention" aria-label={asset === "fund" ? "基金列表" : "美股列表"}>
          <div className="bc-attention-heading"><div className="bc-attention-scope"><h3>{asset === "us_equity" ? "美股自选" : mode === "positions" ? "手工持有" : "基金自选"}</h3>{asset === "fund" && <span className="bc-list-count">{rows.length} 只</span>}</div></div>
          <p className="asset-note">{asset === "fund" ? "顶部搜索基金名称或代码；查看后可加入自选。" : "美股行情来源通过核验后，才开放搜索与详情。"}</p>
          {records.error != null && asset === "fund" && <p role="alert" className="asset-error">记录读取失败；保留值仅供参考。<button className="bc-compact-button" onClick={records.refresh}>重试记录</button></p>}
          <div className="bc-attention-track" tabIndex={0} aria-label="资产列表，可滚动">
            {asset === "fund" && records.pending && !records.data ? <Skeleton className="h-20 w-full"/> : rows.map(item => <article key={item.instrument_id} className="bc-attention-item" data-selected={sourceSymbol === item.source_symbol}><button className="bc-attention-select asset-list-select" aria-pressed={sourceSymbol === item.source_symbol} onClick={() => select(item.source_symbol)}><span className="bc-attention-identity"><strong>{item.name || item.source_symbol}</strong><span>{item.currency}</span></span><span className="asset-note">{item.source_symbol}</span>{item.units != null && <span className="asset-note">{item.units.toLocaleString("zh-CN")} 份 · 手工记录</span>}</button></article>)}
            {asset === "fund" && records.data && !rows.length && <p className="bc-watch-empty">{mode === "positions" ? "还没有持有记录。选中基金后可录入实际份额与同币种总成本。" : "还没有基金自选。搜索基金、核对份额后再添加。"}</p>}
          </div>
        </section>
      </aside>
      <section className="bc-workbench-detail" aria-label="资产详情">
        {capability.error != null ? <div className="asset-empty" role="alert"><h2>资产能力读取失败</h2><p>未发起行情请求，请重试能力核验。</p><button className="bc-compact-button" onClick={capability.refresh}>重新读取</button></div>
          : !available ? capability.data ? <div className="asset-empty" role="alert"><h2>未返回该资产能力</h2><p>当前不能确认读取支持，未发起行情请求。</p><button className="bc-compact-button" onClick={capability.refresh}>重新核对能力</button></div> : <div className="asset-empty" role="status"><Skeleton className="h-20 w-full"/><p>正在核对资产能力…</p></div>
          : available.state === "blocked" ? <div className="asset-empty"><h2>{asset === "us_equity" ? "美股来源待核验" : "基金来源暂不可用"}</h2><p>{available.reason}</p><p className="asset-note">{asset === "us_equity" ? "不借用 A 股行情、不绘制估算价格，也不参与 A 股选股。" : "保留已有自选与记录，不以其他币种或基金替代。"}</p><button className="bc-compact-button" onClick={capability.refresh}>重新核对能力</button></div>
          : asset === "us_equity" ? <div className="asset-empty"><h2>美股详情尚未开放</h2><p>当前界面未接入已验证的美股读取合同。</p></div>
          : !valid ? <div className="asset-empty"><h2>{sourceSymbol ? "基金身份无法确认" : "选择一只基金"}</h2><p>{sourceSymbol ? "需要完整的官方份额代码，例如 040046.OF；不会按同号 A 股读取。" : "从顶部搜索或左侧自选打开，查看官方净值、披露持仓及费用。"}</p><p className="asset-note">净值按披露日期展示，不是盘中实时估值。</p></div>
          : identityMismatch ? <div className="asset-empty" role="alert"><h2>返回身份不一致</h2><p>已停止展示该结果，未套用其他基金数据。</p><button className="bc-compact-button" onClick={snapshot.refresh}>重新读取</button></div>
          : !data ? <div className="asset-empty" role={snapshot.error ? "alert" : "status"}>{snapshot.error ? <><h2>基金资料读取失败</h2><p>{snapshot.error instanceof Error ? snapshot.error.message : "请重试"}</p><button className="bc-compact-button" onClick={snapshot.refresh}>重新读取基金</button></> : <><Skeleton className="h-48 w-full"/><p>正在读取官方净值与披露资料…</p></>}</div>
          : <div className="asset-detail-owner">{snapshot.error != null && <p role="alert" className="asset-error">资料刷新失败，以下为上次读取结果，接收于 {data.received_at}。</p>}<FundDetail key={data.instrument.instrument_id} snapshot={data} range={range} setRange={setRange} record={selectedRecord} recordPending={!records.data || records.pending || records.error != null} onRefreshRecords={records.refresh} refreshing={snapshot.pending} onRefresh={snapshot.refresh}/></div>}
      </section>
    </div>
  </WorkbenchFrame>;
}
