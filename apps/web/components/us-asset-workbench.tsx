"use client";

import {useEffect, useRef, useState} from "react";
import {useRouter, useSearchParams} from "next/navigation";
import {WorkbenchFrame} from "@/components/workbench-frame";
import {UsDailyChart} from "@/components/us-daily-chart";
import {Skeleton} from "@/components/ui/loading";
import {useResource} from "@/hooks/use-resource";
import {ApiError} from "@/lib/api/client";
import {getAssetCapabilities, type AssetEvidence} from "@/lib/api/assets";
import {deleteUsWatchRecord, getUsAssetRecords, getUsSnapshot, saveUsWatchRecord, usInstrumentMatches, validUsSourceSymbol, type UsAssetRecord, type UsSnapshot} from "@/lib/api/us-assets";
import {ASSET_RECORDS_EVENT, assetWorkbenchUrl, notifyAssetRecordsChanged} from "@/lib/asset-identity";

function validRecord(row: UsAssetRecord) {
  return row.asset_type === "us_equity" && row.currency === "USD" && validUsSourceSymbol(row.source_symbol)
    && row.instrument_id === `us_equity:US:${row.source_symbol}:USD`;
}
function number(value: number | null, digits = 2) {return value == null || !Number.isFinite(value) ? "--" : value.toLocaleString("zh-CN", {minimumFractionDigits: digits, maximumFractionDigits: digits});}
function signed(value: number | null) {return value == null || !Number.isFinite(value) ? "--" : `${value > 0 ? "+" : ""}${number(value)}`;}
function tone(value: number | null) {return value == null || value === 0 ? "" : value > 0 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down";}
function clock(value: string, zone: string) {
  if (!/(?:Z|[+-]\d{2}:\d{2})$/.test(value) || !Number.isFinite(Date.parse(value))) return "来源时区待核";
  return new Intl.DateTimeFormat("zh-CN", {timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false}).format(new Date(value)).replaceAll("/", "-");
}
function Evidence({items}: {items: AssetEvidence[]}) {
  return items.length > 0 && <details className="asset-evidence"><summary>来源核对 · {items.length} 份回执</summary>{items.map(item => <p key={item.receipt_id}>腾讯 · 接收 {clock(item.received_at, "Asia/Shanghai")}（北京）<span className="asset-receipt">回执 {item.receipt_id} · SHA-256 {item.payload_sha256}</span></p>)}</details>;
}

function Overnight({data}: {data: UsSnapshot}) {
  const observation = data.overnight_data;
  if (!observation) return <div className="asset-chart-empty" role="status">夜盘暂无可核数据：{data.overnight.reason}。不以常规收盘价代替。</div>;
  const identity = observation.instrument_id === data.instrument.instrument_id && observation.symbol === data.instrument.symbol
    && observation.source === "alpaca" && observation.feed === "overnight" && observation.session === "overnight";
  const quote = identity && observation.authorization_verified && observation.quote?.currency === "USD"
    && observation.quote.source === "alpaca" && observation.quote.feed === "overnight"
    && observation.quote.price_kind === "indicative_bid_ask" && observation.quote.last_trade_available === false ? observation.quote : null;
  const labels: Record<string, string> = {disabled: "未启用夜盘来源", missing_credentials: "待保存免费行情 Key", authorization_unverified: "账号行情权限尚未通过实测", unavailable: "本次未取得有效夜盘报价", stale: "本次读取的夜盘报价已陈旧", available: "本次读取通过夜盘报价核对"};
  return <section className="asset-night-reading" aria-label="夜盘指示性报价"><h3>{observation.state === "available" && !quote ? "夜盘返回未通过核对" : labels[observation.state] ?? "夜盘状态待核"}</h3>
    {quote && <><dl className="asset-facts"><div><dt>指示性买价（USD）</dt><dd>{number(quote.bid_price)}</dd></div><div><dt>指示性卖价（USD）</dt><dd>{number(quote.ask_price)}</dd></div><div><dt>买量 / 卖量（股）</dt><dd>{number(quote.bid_size_shares, 0)} / {number(quote.ask_size_shares, 0)}</dd></div></dl><p className="asset-note">源钟 {clock(quote.source_time, "America/New_York")}（纽约） / {clock(quote.source_time, "Asia/Shanghai")}（北京）</p></>}
    {observation.quote && !quote && <p className="asset-error" role="alert">夜盘身份、币种或价格类型未通过核对，未展示该报价。</p>}
    <p className="asset-note">Alpaca 派生夜盘来源 · 仅指示性买卖报价，不是最新成交价或全市场合并行情。</p>
    <details className="asset-evidence"><summary>本次状态与源钟</summary><p>核对 {clock(observation.checked_at, "Asia/Shanghai")}（北京） · {observation.reason}</p><p>{observation.calendar_note}</p>{observation.missing.map(item => <p key={item}>缺少：{item}</p>)}{observation.evidence.map(item => <p key={item.receipt_id} className="asset-receipt">Alpaca · 接收 {clock(item.received_at, "Asia/Shanghai")} · 回执 {item.receipt_id} · SHA-256 {item.payload_sha256}</p>)}</details>
  </section>;
}

export function UsAssetWorkbench() {
  const params = useSearchParams(), router = useRouter();
  const symbol = params.get("symbol")?.trim().toUpperCase() ?? null;
  const [mobileView, setMobileView] = useState<"list" | "chart">(symbol ? "chart" : "list");
  const [appliedSymbol, setAppliedSymbol] = useState(symbol);
  if (symbol !== appliedSymbol) {setAppliedSymbol(symbol); if (symbol) setMobileView("chart");}
  const capability = useResource(signal => getAssetCapabilities({signal}), {key: "asset-capabilities", abortable: true, refreshOnVisible: false});
  const available = capability.data?.items.find(item => item.asset_type === "us_equity");
  const records = useResource(signal => getUsAssetRecords({signal}), {key: "us-asset-records", abortable: true, refreshOnVisible: false});
  useEffect(() => {window.addEventListener(ASSET_RECORDS_EVENT, records.refresh); return () => window.removeEventListener(ASSET_RECORDS_EVENT, records.refresh);}, [records.refresh]);
  const valid = validUsSourceSymbol(symbol);
  const snapshot = useResource(signal => getUsSnapshot(symbol!, {signal}), {key: `us:${symbol ?? ""}:120`, enabled: valid && available?.state === "available", abortable: true, refreshOnVisible: false});
  const wrongIdentity = snapshot.data && (!usInstrumentMatches(snapshot.data.instrument) || snapshot.data.instrument.source_symbol !== symbol);
  const data = wrongIdentity ? undefined : snapshot.data;
  const selectedRecord = records.data?.items.find(row => validRecord(row) && row.instrument_id === data?.instrument.instrument_id);
  const rows = records.data?.items.filter(row => row.is_watched) ?? [];
  function select(code: string) {setMobileView("chart"); router.replace(assetWorkbenchUrl("us_equity", code), {scroll: false});}
  return <WorkbenchFrame asset="us_equity" actions={<span className="asset-note">仅查看与自选</span>}>
    <nav className="bc-mobile-views" aria-label="工作台内容"><button aria-pressed={mobileView === "list"} onClick={() => setMobileView("list")}>资产列表</button><button aria-pressed={mobileView === "chart"} onClick={() => setMobileView("chart")}>详情</button></nav>
    <div className="bc-workbench-layout" data-mobile-view={mobileView}>
      <aside className="bc-workbench-sidebar" aria-label="美股资产列表"><section className="bc-attention" aria-label="美股自选列表">
        <div className="bc-attention-heading"><div className="bc-attention-scope"><h3>美股自选</h3><span className="bc-list-count">{records.data ? `${rows.length} 只` : records.error ? "读取失败" : records.pending ? "读取中" : "待核记录"}</span></div><button className="bc-compact-button" disabled={records.pending} onClick={records.refresh}>重读自选</button></div>
        <p className="asset-note">顶部搜索代码或名称；点选后核对来源、币种和时段。</p>
        {records.error != null && <p className="asset-error" role="alert">自选读取失败；已有记录保留，未当作空列表。</p>}
        <div className="bc-attention-track" tabIndex={0} aria-label="美股自选，可滚动">
          {!records.data && records.pending ? <Skeleton className="h-20 w-full"/> : rows.map(row => {
            const verified = validRecord(row), active = verified && row.instrument_id === data?.instrument.instrument_id;
            return <article key={row.instrument_id} className="bc-attention-item" data-selected={active}><button className="bc-attention-select asset-list-select" disabled={!verified} aria-pressed={active} onClick={() => select(row.source_symbol)}><span className="bc-attention-identity"><strong>{active ? data.instrument.name : row.name || row.source_symbol}</strong><span>{row.currency}</span></span><span className="asset-note">{row.source_symbol} · {active ? "腾讯来源名称" : "保存名称"}</span>{active && row.name && row.name !== data.instrument.name && <span className="asset-note">记录名：{row.name}</span>}{!verified && <span className="asset-note">保存身份待核，未按其他市场打开</span>}</button></article>;
          })}
          {!!records.data && !rows.length && <p className="bc-watch-empty">还没有美股自选。搜索后可查看来源报价并加入自选。</p>}
        </div>
      </section></aside>
      <section className="bc-workbench-detail" aria-label="美股详情">
        {capability.error != null ? <div className="asset-empty" role="alert"><h2>美股能力读取失败</h2><button className="bc-compact-button" onClick={capability.refresh}>重新核对能力</button></div>
          : !available ? <div className="asset-empty" role="status"><h2>{capability.data ? "未返回美股读取能力" : "正在核对美股来源…"}</h2>{!!capability.data && <button className="bc-compact-button" onClick={capability.refresh}>重新核对能力</button>}</div>
          : available.state === "blocked" ? <div className="asset-empty" role="alert"><h2>美股来源暂不可用</h2><p>{available.reason}</p><button className="bc-compact-button" onClick={capability.refresh}>重新核对来源</button></div>
          : !valid ? <div className="asset-empty"><h2>{symbol ? "美股身份无法确认" : "选择一只美股"}</h2><p>从顶部搜索或左侧自选打开完整来源代码；不借用同名 A 股或基金。</p></div>
          : wrongIdentity ? <div className="asset-empty" role="alert"><h2>返回身份或币种不一致</h2><p>已停止展示，未套用其他证券的报价。</p><button className="bc-compact-button" onClick={snapshot.refresh}>重新读取美股</button></div>
          : !data ? <div className="asset-empty" role={snapshot.error ? "alert" : "status"}>{snapshot.error ? <><h2>美股资料读取失败</h2><p>{snapshot.error instanceof Error ? snapshot.error.message : "请重试"}</p><button className="bc-compact-button" onClick={snapshot.refresh}>重新读取美股</button></> : <><Skeleton className="h-48 w-full"/><p>正在读取来源报价与日K…</p></>}</div>
          : <div className="asset-detail-owner">{snapshot.error != null && <p className="asset-error" role="alert">刷新失败，以下保留上次读取；接收 {clock(data.received_at, "Asia/Shanghai")}（北京）。</p>}<UsDetail key={data.instrument.instrument_id} data={data} record={selectedRecord} recordPending={!records.data || records.pending || records.error != null} onRefreshRecords={records.refresh} refreshing={snapshot.pending} onRefresh={snapshot.refresh}/></div>}
      </section>
    </div>
  </WorkbenchFrame>;
}

function UsDetail({data, record, recordPending, onRefreshRecords, refreshing, onRefresh}: {data: UsSnapshot; record?: UsAssetRecord; recordPending: boolean; onRefreshRecords: () => void; refreshing: boolean; onRefresh: () => void}) {
  const [tab, setTab] = useState<"daily" | "minute" | "pre_market" | "post_market" | "overnight">("daily");
  const [busy, setBusy] = useState(false), [error, setError] = useState("");
  const lock = useRef(false), {instrument, regular} = data;
  const unsupportedRecord = record && (record.units != null || record.cost_total != null);
  async function toggleWatch() {
    if (busy || lock.current || recordPending || unsupportedRecord) return;
    lock.current = true; setBusy(true); setError("");
    try {
      if (record?.is_watched) await deleteUsWatchRecord(record.instrument_id, record.version);
      else await saveUsWatchRecord(instrument, record?.version ?? 0, record);
      notifyAssetRecordsChanged(); onRefreshRecords();
    } catch (e) {setError(e instanceof ApiError && e.status === 409 ? "自选已有新版本，请重新读取后再操作。" : `自选保存失败：${e instanceof Error ? e.message : "请重试"}`);}
    finally {lock.current = false; setBusy(false);}
  }
  const observation = regular.observation === "regular_close" ? "常规时段收盘价" : regular.observation === "stale" ? "常规时段历史报价 · 已过期" : regular.observation === "session_unverified" ? "来源报价 · 时段待核" : "常规时段报价";
  const phase = {pre_market: "盘前", regular: "常规时段", post_market: "盘后", overnight: "夜间", weekend: "周末"}[data.clock_phase];
  const dailyValid = data.daily.source_symbol === instrument.source_symbol && data.daily.source === "tencent" && data.daily.price_basis === "none";
  const tabs = [{key: "daily", label: "日K"}, {key: "minute", label: "分时"}, {key: "pre_market", label: "盘前"}, {key: "post_market", label: "盘后"}, {key: "overnight", label: "夜盘"}] as const;
  return <div className="asset-detail">
    <header className="asset-detail-heading"><div><h2>{instrument.name}</h2><p>{instrument.source_symbol} · USD{instrument.security_type === "etf" ? " · ETF" : ""} · 腾讯{instrument.english_name ? ` · ${instrument.english_name}` : ""}</p></div><div className="asset-actions"><button className="bc-compact-button" disabled={recordPending || busy || !!unsupportedRecord} onClick={() => void toggleWatch()}>{busy ? "保存中…" : record?.is_watched ? "移出美股自选" : "加入美股自选"}</button><button className="bc-compact-button" disabled={refreshing} onClick={onRefresh}>{refreshing ? "读取中…" : "刷新资料"}</button></div></header>
    {error && <p className="asset-error" role="alert">{error}<button className="bc-compact-button" onClick={onRefreshRecords}>重新读取自选</button></p>}
    {unsupportedRecord && <p className="asset-error" role="alert">旧记录含未支持的持有字段，已停止覆盖；行情仍可查看。</p>}
    <nav className="asset-detail-tabs" aria-label="美股时段与图表">{tabs.map(item => <button key={item.key} type="button" aria-pressed={tab === item.key} onClick={() => setTab(item.key)}>{item.label}</button>)}</nav>
    <div className="asset-reading-pane" tabIndex={0} aria-label={tabs.find(item => item.key === tab)!.label}>
      <div className="asset-us-quote"><div><span className="asset-note">{observation}</span><strong className={`asset-us-price ${tone(regular.change_pct)}`}>{number(regular.price)} <small>USD</small></strong></div><span className={tone(regular.change_pct)}>{signed(regular.change)} · {signed(regular.change_pct)}%</span></div>
      <p className="asset-note">源钟 {clock(regular.source_time, "America/New_York")}（纽约） / {clock(regular.source_time, "Asia/Shanghai")}（北京）</p>
      <p className="asset-note">腾讯 · 延迟未核 · 对照来源前一常规收盘</p>
      <details className="asset-evidence"><summary>纽约时钟：{phase} · 日历待核</summary><p>{data.calendar_note}</p></details>
      {tab === "daily" ? <>
        <dl className="asset-facts"><div><dt>前一常规收盘（USD）</dt><dd>{number(regular.previous_close)}</dd></div><div><dt>开盘（USD）</dt><dd>{number(regular.open)}</dd></div><div><dt>最高 / 最低（USD）</dt><dd>{number(regular.high)} / {number(regular.low)}</dd></div><div><dt>成交量（股）</dt><dd>{number(regular.volume_shares, 0)}</dd></div><div><dt>成交额（USD）</dt><dd>{number(regular.turnover)}</dd></div></dl>
        {!dailyValid ? <div className="asset-chart-empty" role="alert">日K身份或价格口径不一致，已停止绘制。</div> : data.daily.state === "unavailable" ? <div className="asset-chart-empty" role="status">日K暂不可用：{data.daily.reason ?? "来源未返回有效序列"}。常规报价仍保留。</div> : <UsDailyChart points={data.daily.points} symbol={instrument.source_symbol}/>}
        <Evidence items={[...regular.evidence, ...data.daily.evidence]}/>
      </> : tab === "overnight" ? <Overnight data={data}/> : <div className="asset-chart-empty" role="status"><span>{tabs.find(item => item.key === tab)!.label}暂无可核数据：{data[tab].reason}。不以常规收盘价代替。</span></div>}
      <p className="asset-note">只提供行情查看与自选，不执行交易或进入 A 股选股。</p>
    </div>
  </div>;
}
