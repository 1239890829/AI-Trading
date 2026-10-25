"use client";

import { useId, useState } from "react";
import Link from "next/link";
import { StockLink } from "@/components/stock-link";
import { ResearchDisclosure } from "@/components/ui/reader-brief";
import { useResource } from "@/hooks/use-polling-fetch";
import { bjDate, dateTimeTextBJ, fmt, fmtAmount, pctColor, pctText, signedYi } from "@/lib/format";
import { readerExcerpt } from "@/lib/reader-copy";
import { getReviewReport, type ReviewData, type ReviewProvenance, type ReviewReportDetail, type ReviewSectorBoard } from "@/lib/api";

const STATE_LABEL: Record<ReviewProvenance["state"], string> = {
  verified: "已核同日", partial: "同日 · 部分覆盖", unverified: "未核同日", unavailable: "未取得", mismatch: "日期不符",
};

const compactDay = (value?: string | null) => value?.replaceAll("-", "") ?? "";
const finite = (value: unknown): number | null => typeof value === "number" && Number.isFinite(value) ? value : null;
const text = (value: unknown) => typeof value === "string" && value.trim() ? value : null;
const strings = (value: unknown): string[] => Array.isArray(value) ? value.filter((v): v is string => typeof v === "string" && !!v.trim()) : [];
const count = (value: unknown) => { const n = finite(value); return n !== null && n >= 0 ? fmt(n, 0) : "未记录"; };
const reviewMomentLabel = (state?: string, at?: string | null) => state === "saved" && at
  && /(?:Z|[+-]\d{2}:?\d{2})$/i.test(at) && Number.isFinite(Date.parse(at)) ? `回看于 ${dateTimeTextBJ(at)}` : "记录时点未核";

/** Report identity alone never proves that a collected value belongs to its date. */
export function reviewSegmentBound(data: ReviewData | undefined, key: string, tradeDate: string, trading = false) {
  const section = trading ? data?.trading : data?.market;
  const p = section?.provenance?.[key];
  return section?.contract_version === "date_bound_v1" && (p?.state === "verified" || p?.state === "partial")
    && compactDay(section.trade_date) === compactDay(tradeDate)
    && compactDay(p.requested_trade_date) === compactDay(tradeDate)
    && compactDay(p.source_trade_date) === compactDay(tradeDate);
}

function currentAccountSnapshot(data: ReviewData | undefined, key: "account" | "positions", tradeDate: string) {
  const section=data?.trading, record=section?.provenance?.[key];
  return section?.contract_version === "date_bound_v1" && !!record && ["verified","partial"].includes(record.state)
    && record.source === (key === "account" ? "paper_account:main" : "paper_position:main")
    && compactDay(section.trade_date) === compactDay(tradeDate) && compactDay(record.requested_trade_date) === compactDay(tradeDate)
    && !!record.received_at && /(?:Z|[+-]\d{2}:?\d{2})$/i.test(record.received_at)
    && compactDay(bjDate(record.received_at)) === compactDay(tradeDate);
}

function SourceRecord({ record, bound, snapshot = false }: {record?: ReviewProvenance; bound: boolean; snapshot?: boolean}) {
  return <details className="market-review-source">
    <summary>来源与时点 · {record ? snapshot && bound ? "采集时刻快照" : !bound && ["verified", "partial"].includes(record.state) ? "日期凭据不符" : STATE_LABEL[record.state] || "状态未核" : "旧记录未保存"}</summary>
    <dl>
      <div><dt>原来源</dt><dd>{record?.source || "未记录"}</dd></div>
      <div><dt>来源时刻</dt><dd>{record?.source_as_of ? dateTimeTextBJ(record.source_as_of) : "未记录，不用接收时刻代替"}</dd></div>
      <div><dt>原日期字段</dt><dd>{record?.source_trade_date || "未记录"}（核验范围见说明）</dd></div>
      <div><dt>接收时刻</dt><dd>{record?.received_at ? dateTimeTextBJ(record.received_at) : "未记录"}</dd></div>
    </dl>
    <p>{record?.reason || "旧报告没有逐段日期、来源时间和质量凭据；原采集值不能升级为本日事实。"}</p>
  </details>;
}

function MissingSegment({label, record}: {label: string; record?: ReviewProvenance}) {
  return <p className="market-review-unavailable">{label}：{record?.reason || "旧记录未保存同日来源凭据，暂不据此判断。"}</p>;
}

function ReportComparison({report, previousDate, active}: {report: ReviewReportDetail; previousDate?: string; active: boolean}) {
  const [open, setOpen] = useState(false);
  const identity = `${report.trade_date}:${report.review_id}:${report.generated_at}:${previousDate ?? ""}`;
  const previous = useResource(() => getReviewReport(previousDate!), {key: identity, enabled: active && open && !!previousDate, intervalMs: null, marketHours: false});
  const currentBound = reviewSegmentBound(report.data, "breadth", report.trade_date);
  const previousBound = !!previous.data && reviewSegmentBound(previous.data.data, "breadth", previous.data.trade_date)
    && compactDay(previous.data.trade_date) === compactDay(previousDate);
  return <div className="market-review-comparison">
    <button type="button" aria-expanded={open} onClick={() => setOpen(!open)} disabled={!previousDate}>
      {open ? "收起日期对照" : previousDate ? `对照上一份 · ${previousDate}` : "暂无更早报告可对照"}
    </button>
    {open && <div className="market-review-comparison-body">
      {previous.pending && !previous.data ? <p role="status">读取原报告…</p> : previous.error ? <p role="alert">原报告读取失败，未用其他日期代替。<button onClick={previous.refresh}>重试对照</button></p> : previous.data && compactDay(previous.data.trade_date) !== compactDay(previousDate) ? <p role="alert">对照报告日期不符，未使用该返回。</p> : previous.data && <>
        <p className="market-review-caption">相邻已存报告，不保证是相邻交易日；只比较已核同日的原始字段。</p>
        <p className="market-review-caption">两份记录的来源与覆盖范围可能不同，不据此直接判断市场改善或策略效果。</p>
        {currentBound && previousBound ? <table className="market-review-table"><caption>{previousDate} → {report.trade_date} 的市场宽度</caption><thead><tr><th>字段</th><th>{previousDate}</th><th>{report.trade_date}</th></tr></thead><tbody>
          {([["上涨家数", "up"], ["下跌家数", "down"], ["涨停家数", "limit_up"], ["跌停家数", "limit_down"]] as const).map(([label, key]) => <tr key={key}><th>{label}</th><td>{count(previous.data?.data?.market.breadth[key])}</td><td>{count(report.data?.market.breadth[key])}</td></tr>)}
        </tbody></table> : <p className="market-review-unavailable">至少一份报告的市场宽度未核同日，不计算跨日变化。</p>}
        <ResearchDisclosure label="上一份报告的原判断" identity={`${identity}:${previous.data.review_id}`}><p>{previous.data.summary || "原报告没有摘要。"}</p></ResearchDisclosure>
      </>}
    </div>}
  </div>;
}

export function MarketReview({report, previousDate, active = true}: {report: ReviewReportDetail; previousDate?: string; active?: boolean}) {
  const prefix = useId();
  const [sector, setSector] = useState<"industry" | "concept">("industry");
  const [sectorOrder, setSectorOrder] = useState<"strong" | "weak">("strong");
  const [allBoards, setAllBoards] = useState(false);
  const data = report.data;
  const market = data?.market;
  const provenance = market?.provenance;
  const bound = (key: string) => reviewSegmentBound(data, key, report.trade_date);
  const breadth = bound("breadth") ? market?.breadth : undefined;
  const up = finite(breadth?.up), down = finite(breadth?.down), flat = finite(breadth?.flat);
  const distribution = up !== null && down !== null && up >= 0 && down >= 0 && (flat === null || flat >= 0) && up + down + (flat ?? 0) > 0
    ? [{label: "上涨", value: up, tone: "up"}, {label: "下跌", value: down, tone: "down"}, ...(flat === null ? [] : [{label: "平盘", value: flat, tone: "flat"}])] : [];
  const distributionTotal = distribution.reduce((n, v) => n + v.value, 0);
  const indices = bound("indices") ? market?.indices ?? [] : [];
  const savedBoardProof=provenance?.sector_boards;
  const referenceAllowed=!bound("sector_boards") && savedBoardProof?.state==="unverified" && savedBoardProof.source==="board_flow.daily"
    && compactDay(savedBoardProof.source_trade_date)===compactDay(report.trade_date) && compactDay(savedBoardProof.requested_trade_date)===compactDay(report.trade_date);
  const savedBoards=market?.reported_values?.sector_boards;
  const rawBoards=savedBoards && typeof savedBoards==="object" && !Array.isArray(savedBoards) ? (savedBoards as Record<string,unknown>)[sector] : undefined;
  const referenceBoards: ReviewSectorBoard[]=Array.isArray(rawBoards) ? rawBoards.flatMap(value=>{
    if (!value || typeof value!=="object" || Array.isArray(value)) return [];
    const row=value as Record<string,unknown>;
    return typeof row.board_code==="string" && typeof row.name==="string" ? [{board_code:row.board_code,name:row.name,change_pct:finite(row.change_pct),main_net_yi:finite(row.main_net_yi)}] : [];
  }) : [];
  const boards = bound("sector_boards") ? market?.sector_boards?.[sector] ?? [] : referenceAllowed ? referenceBoards : [];
  const sortedBoards = [...boards].sort((a,b) => {
    const av=finite(a.change_pct), bv=finite(b.change_pct);
    if (av===null) return bv===null ? 0 : 1;
    if (bv===null) return -1;
    return (sectorOrder === "strong" ? -1 : 1) * (av-bv);
  });
  const picks = data?.picks;
  const picksBound = picks && compactDay(picks.trade_date) === compactDay(report.trade_date) && compactDay(picks.combo_date) === compactDay(report.trade_date);
  const ordersBound = reviewSegmentBound(data, "orders", report.trade_date, true);
  const orders = ordersBound ? data?.trading.orders ?? [] : [];
  const accountBound = currentAccountSnapshot(data, "account", report.trade_date);
  const positionsBound = currentAccountSnapshot(data, "positions", report.trade_date);
  const marketDimension = report.dimensions.find(d => d.key === "market");
  const outlook = report.dimensions.flatMap(d => d.key === "market" || d.key === "picks" ? d.judgements.map(value => ({title:d.title, value})) : []);
  const savedOutlook = data?.outlook?.state === "saved" && compactDay(data.outlook.trade_date) === compactDay(report.trade_date) ? data.outlook : undefined;
  const journey = data?.selection_journey && compactDay(data.selection_journey.trade_date) === compactDay(report.trade_date) ? data.selection_journey : undefined;
  const sections = [["盘面", "market"], ["主线", "sectors"], ["原入选与交易", "picks"], ["后续观察", "outlook"]] as const;
  return <div className="market-review">
    <div className="market-review-heading"><div><h3>这一天，发生了什么</h3><p>{report.trade_date} · 报告保存于 {dateTimeTextBJ(report.generated_at)}</p></div><ReportComparison report={report} previousDate={previousDate} active={active}/></div>
    <nav className="market-review-jumps" aria-label="市场复盘内容定位">{sections.map(([label,key]) => <button key={key} type="button" onClick={() => document.getElementById(`${prefix}-${key}`)?.scrollIntoView({block:"start",behavior:"auto"})}>{label}</button>)}</nav>
    {!data && <p className="market-review-unavailable">这份报告没有保存结构化采集数据。可切换“检查与改进”读取原报告，不用当前行情补写过去。</p>}
    {data && market?.contract_version !== "date_bound_v1" && <p className="market-review-unavailable">旧报告未保存逐段来源时点。原文保留，以下不把旧采集值当作已核实的同日盘面。</p>}
    <section id={`${prefix}-market`} className="market-review-section" aria-label="盘面事实">
      <div className="market-review-section-title"><h4>盘面事实</h4><span>事实、判断与缺失分开</span></div>
      {breadth ? <><dl className="market-review-facts">
        <div><dt>上涨 / 下跌</dt><dd><span className="text-up-ink dark:text-up">{count(breadth.up)}</span><span> / </span><span className="text-down-ink dark:text-down">{count(breadth.down)}</span></dd></div>
        <div><dt>涨停 / 跌停</dt><dd>{count(breadth.limit_up)} / {count(breadth.limit_down)}</dd></div>
        <div><dt>成交额</dt><dd>{fmtAmount(finite(breadth.total_amount))}</dd></div>
        <div><dt>已记录覆盖</dt><dd>{count(breadth.total)} 家</dd></div>
      </dl>{distribution.length > 0 && <div className="market-review-breadth" role="img" aria-label={`已记录涨跌分布：${distribution.map(v=>`${v.label}${count(v.value)}家`).join("，")}${flat === null ? "；平盘未记录" : ""}`}>
        {distribution.map(v => <span key={v.tone} data-tone={v.tone} style={{flexGrow:v.value / distributionTotal}}/>)}
      </div>}</> : <MissingSegment label="市场宽度" record={provenance?.breadth}/>}
      <SourceRecord record={provenance?.breadth} bound={bound("breadth")}/>
      {indices.length ? <div className="market-review-indices">{indices.map(q => <div key={q.symbol}><span>{q.name || q.symbol}</span><strong className={pctColor(q.change_pct)}>{pctText(q.change_pct)}</strong><span>{fmt(q.close)}</span></div>)}</div> : <MissingSegment label="指数" record={provenance?.indices}/>}
      <SourceRecord record={provenance?.indices} bound={bound("indices")}/>
      {bound("sentiment") ? <p className="market-review-phase">原情绪状态：<strong>{text(market?.sentiment.phase) || "未记录"}</strong>{text(market?.sentiment.confidence) ? ` · ${text(market?.sentiment.confidence)}` : ""}。状态不是上涨概率。</p> : <MissingSegment label="情绪状态" record={provenance?.sentiment}/>}
      <SourceRecord record={provenance?.sentiment} bound={bound("sentiment")}/>
      <ResearchDisclosure label="原报告的市场判断" identity={`${report.review_id}:market:${report.generated_at}`}><ul>{marketDimension?.judgements.map((v,i)=><li key={i}>{v}</li>)}</ul>{!marketDimension?.judgements.length && <p>原报告没有市场判断。</p>}<p>原判断按原报告保存，不因后来行情改写；来源未核时不升级结论。</p></ResearchDisclosure>
    </section>
    <section id={`${prefix}-sectors`} className="market-review-section" aria-label="行业与概念主线">
      <div className="market-review-section-title"><h4>{referenceAllowed ? "板块采集参考" : "主线强弱"}</h4><Link href="/market?tab=fund">核对当前板块资金</Link></div>
      {referenceAllowed && <p className="market-review-caption">原数据源按日保存的资金Top50样本，缺逐行源钟。以下只重排这份采集参考，不是已核同日的全市场领涨/领跌榜。</p>}
      <div className="market-review-controls"><div role="group" aria-label="板块分类">{(["industry","concept"] as const).map(key=><button key={key} type="button" aria-pressed={sector===key} onClick={()=>{setSector(key);setAllBoards(false);}}>{key==="industry"?"行业":"概念"}</button>)}</div><div role="group" aria-label="板块排序"><button type="button" aria-pressed={sectorOrder==="strong"} onClick={()=>setSectorOrder("strong")}>涨幅优先</button><button type="button" aria-pressed={sectorOrder==="weak"} onClick={()=>setSectorOrder("weak")}>跌幅优先</button></div></div>
      {sortedBoards.length ? <><ol className="market-review-board-list">{sortedBoards.slice(0,allBoards?undefined:8).map((board,i)=><li key={`${sector}:${board.board_code}`}><span className="market-review-rank">{i+1}</span><strong>{board.name}</strong><span className={pctColor(board.change_pct)}>{pctText(board.change_pct)}</span><span title="主力净额（亿元），不是该板块全部资金">{signedYi(board.main_net_yi)}</span></li>)}</ol><p className="market-review-caption">本报告已存 {boards.length} 条{boards.length>8 && <button type="button" aria-expanded={allBoards} onClick={()=>setAllBoards(!allBoards)}>{allBoards?"只看前8条":"展示本报告全部记录"}</button>}；有限存档不代表全市场全集。</p></> : <MissingSegment label={sector==="industry"?"行业榜":"概念榜"} record={provenance?.sector_boards}/>}
      <SourceRecord record={provenance?.sector_boards} bound={bound("sector_boards")}/>
      {bound("themes") && !!market?.themes.length && <div className="market-review-themes"><h5>原题材记录</h5>{market.themes.map((theme,i)=><span key={`${text(theme.theme)}:${i}`}>{text(theme.name) || text(theme.theme) || "未命名题材"}</span>)}</div>}
      <SourceRecord record={provenance?.themes} bound={bound("themes")}/>
    </section>
    <section id={`${prefix}-picks`} className="market-review-section" aria-label="原入选与模拟记录">
      <div className="market-review-section-title"><h4>我的原入选与模拟</h4><Link href="/hunting?view=review">核对原入选与执行</Link></div>
      <p className="market-review-caption">入选、价格观察、委托和成交分别留痕；研究完成不等于可以买入。</p>
      {picksBound ? picks.items.length ? <div className="market-review-pick-list">{picks.items.map(item=>{
        const review=picks.reviews.find(r=>r.symbol===item.symbol);
        return <article key={item.symbol}><div><StockLink symbol={item.symbol}>{item.name || item.symbol}<span className="market-review-symbol"> {item.symbol}</span></StockLink><span>{item.observation_only?"仅观察":"当日持久入选"}</span></div><p>{item.basis ? readerExcerpt(item.basis,120) : item.themes.length?item.themes.join(" · "):"原理由未记录"}</p>{review ? <p>{review.statistics_eligible?"同版价格观察":"未进入可信观察统计"} · {readerExcerpt(review.note || review.reason_category,100)}{review.statistics_eligible && finite(review.excess_pct)!==null ? ` · 超额 ${pctText(review.excess_pct)}` : ""}</p> : <p>逐股归因未记录，不能当作成功或失败。</p>}</article>;
      })}</div> : <p className="market-review-empty">本报告没有可展示的精选条目；不证明该日零入选。</p> : <p className="market-review-unavailable">没有与报告日期匹配的原入选快照，不借用此前或当前名单。</p>}
      <div className="market-review-trade-summary"><span>本日创建委托 <strong>{ordersBound?orders.length:"未核"}</strong></span><span>已保存成交状态 <strong>{ordersBound?orders.filter(o=>o.status==="filled").length:"未核"}</strong></span><span>已保存拒绝状态 <strong>{ordersBound?orders.filter(o=>o.status==="rejected").length:"未核"}</strong></span></div>
      <p className="market-review-caption">按委托创建日归属；状态是报告保存时的记录，不代表本日新增成交或已实现收益。</p>
      {orders.length > 0 && <ResearchDisclosure label="逐笔原委托与拒绝原因" identity={`${report.review_id}:orders:${report.generated_at}`}><ul className="market-review-order-list">{orders.map(order=><li key={order.id}><StockLink symbol={order.symbol}>{order.symbol}</StockLink> · {order.side==="buy"?"买入":order.side==="sell"?"卖出":order.side} {order.quantity}股 · {order.status} · {order.reason || "未记录原因"}{order.status==="filled"?` · 成交价 ${fmt(order.filled_price)}`:""}</li>)}</ul></ResearchDisclosure>}
      <SourceRecord record={data?.trading.provenance?.orders} bound={ordersBound}/>
      <div className="market-review-journey"><h5>原入选 → 提醒 → 执行</h5>{journey?.items.length ? <>{journey.items.map(item=><article key={`${item.selection_ref}:${item.origin_event_id}`}>
        <div className="market-review-journey-heading"><StockLink symbol={item.symbol}>{item.name || item.symbol}</StockLink><span>{item.selection_source || "来源未记录"}</span></div>
        <p>{readerExcerpt(item.basis,120) || "原入选依据未保存。"}</p>
        <dl className="market-review-journey-states"><div><dt>站内留痕</dt><dd>{item.notification.in_app_recorded?"原记录已保存":"未核"}</dd></div><div><dt>外部送达 / 已读</dt><dd>未核 / 未核</dd></div><div><dt>执行</dt><dd>{item.execution.state || "未核"}</dd></div></dl>
        <p>{item.execution.reason || "未记录执行理由；不能推断未成交的原因。"}</p>
        {item.execution.order && <p className="market-review-caption">原委托 {finite(item.execution.order.order_id) ?? "身份未核"} · {text(item.execution.order.status) || "状态未核"}{text(item.execution.order.status)==="filled" ? ` · 成交价 ${fmt(finite(item.execution.order.filled_price))}`:""}</p>}
        <ResearchDisclosure label={`${item.name || item.symbol}的原身份与通道记录`} identity={`${report.review_id}:${item.selection_ref}:${item.origin_event_id}`}><dl><div><dt>原入选身份</dt><dd>{item.selection_ref}</dd></div><div><dt>来源版本</dt><dd>{item.source_version}</dd></div><div><dt>来源时刻</dt><dd>{item.source_as_of?dateTimeTextBJ(item.source_as_of):"未记录"}</dd></div><div><dt>关联证据</dt><dd>{item.event_refs.length}条原引用；数量不证明关联正确</dd></div></dl>{item.notification.outbox.length ? <ul>{item.notification.outbox.map(o=><li key={o.id}>{o.channel} · {o.state} · {o.reason || "原因未记录"}{o.accepted_at?` · 受理于 ${dateTimeTextBJ(o.accepted_at)}`:""}</li>)}</ul> : <p>没有绑定的通道记录，不等于已送达或未发送。</p>}</ResearchDisclosure>
      </article>)}<p className="market-review-caption">{journey.reason}</p></> : <p className="market-review-unavailable">{journey?.reason || "没有同日同版本的入选通知/执行绑定记录，不按同一股票代码猜关联。"}</p>}</div>
      <ResearchDisclosure label="采集时刻账户与持仓（不作历史收盘估值）" identity={`${report.review_id}:account:${report.generated_at}`}>
        {accountBound ? <dl><div><dt>账户身份</dt><dd>{text(data?.trading.account.name) || "主模拟账户"}</dd></div><div><dt>现金</dt><dd>{fmtAmount(finite(data?.trading.account.cash))}</dd></div></dl> : <MissingSegment label="账户快照" record={data?.trading.provenance?.account}/>}
        {positionsBound ? data?.trading.positions.length ? <ul>{data.trading.positions.map(p=><li key={p.symbol}><StockLink symbol={p.symbol}>{p.symbol}</StockLink> · {p.quantity}股 · 采集价 {fmt(p.last_price)}</li>)}</ul> : <p>该次采集的主模拟持仓为空。</p> : <MissingSegment label="持仓快照" record={data?.trading.provenance?.positions}/>}
        <SourceRecord record={data?.trading.provenance?.account} bound={accountBound} snapshot/>
        <SourceRecord record={data?.trading.provenance?.positions} bound={positionsBound} snapshot/>
      </ResearchDisclosure>
    </section>
    <section id={`${prefix}-outlook`} className="market-review-section" aria-label="后续观察与限制">
      <div className="market-review-section-title"><h4>接下来核对什么</h4><span>读原判断，不新增预测</span></div>
      {savedOutlook?.directions.length ? <><p className="market-review-caption">原简报 {savedOutlook.generated_at ? dateTimeTextBJ(savedOutlook.generated_at):"生成时刻未记录"} · {savedOutlook.source}。不是本次新生成的明日预测。</p><div className="market-review-outlook-list">{savedOutlook.directions.map((direction,i)=><article key={`${direction.direction}:${i}`}><h5>{direction.direction || "原方向未命名"}</h5><p>{direction.logic || "原逻辑未记录"}</p><dl><div><dt>原依据</dt><dd>{direction.basis || "未记录"}</dd></div><div><dt>参与条件</dt><dd>{direction.entry_basis || direction.entry_mode || "未记录"}</dd></div></dl><div className="market-review-condition-columns"><div><h6>等待确认</h6>{strings(direction.trigger_conditions).length ? <ul>{strings(direction.trigger_conditions).map((v,j)=><li key={j}>{v}</li>)}</ul>:<p>原记录未列确认条件。</p>}</div><div><h6>失效 / 反证</h6>{strings(direction.falsify_conditions).length ? <ul>{strings(direction.falsify_conditions).map((v,j)=><li key={j}>{v}</li>)}</ul>:<p>原记录未列失效条件。</p>}</div></div>{direction.review ? <p className="market-review-caption">已存后验回看（{reviewMomentLabel(direction.review_state,direction.reviewed_at)}）：{text(direction.review.outcome) || "结果未记录"}{finite(direction.review.actual_pct)!==null?` · 板块 ${pctText(finite(direction.review.actual_pct))}`:""}{text(direction.review.note)?` · ${text(direction.review.note)}`:""}</p> : <p className="market-review-caption">原方向的条件兑现结果尚未记录。</p>}</article>)}</div><Link href="/hunting?view=discover&sec=brief">核对原简报与方向记录</Link></> : <p className="market-review-unavailable">原简报后续观察：{data?.outlook?.reason || "未保存与报告日期匹配的原方向，不补写预测。"}</p>}
      {outlook.length ? <ResearchDisclosure label="原报告判断与后续建议" identity={`${report.review_id}:outlook:${report.generated_at}`}><ul className="market-review-observation-list">{outlook.map((v,i)=><li key={`${v.title}:${i}`}><span>{v.title}</span><p>{v.value}</p></li>)}</ul></ResearchDisclosure> : <p className="market-review-empty">原报告也未保存后续判断。没有补写明日方向或目标价。</p>}
      <p className="market-review-caption">没有同版确认结果时保留未记录，不按后来涨跌猜条件是否兑现。</p>
      <ResearchDisclosure label="原始采集值与全部缺口" identity={`${report.review_id}:raw:${report.generated_at}`}>
        <p>未核值仅作恢复和原记录阅读，不作为报告当日已核事实。</p>
        {market?.indices.length ? <ul>{market.indices.map(q=><li key={q.symbol}>{q.name || q.symbol} · 原采集值 {fmt(q.close)} / {pctText(q.change_pct)} · {bound("indices")?"同日段已核":"来源日期未核"}</li>)}</ul> : <p>没有保存原指数采集值。</p>}
        {Array.isArray(market?.reported_values?.indices) && <ul>{market.reported_values.indices.map((value,i)=>{const row=value && typeof value==="object" ? value as Record<string,unknown> : {};return <li key={i}>{text(row.symbol) || "证券身份未记录"} · 被隔离原采集值 {fmt(finite(row.price))} / {pctText(finite(row.change_pct))} · 未作为本日事实</li>;})}</ul>}
        <ul>{[...(market?.gaps ?? []),...(data?.trading.gaps ?? []),...(picks?.gaps ?? [])].map((gap,i)=><li key={`${gap.field}:${i}`}>{gap.field}：{gap.reason}{gap.impact?`；影响 ${gap.impact}`:""}</li>)}</ul>
        {!market?.gaps.length && !data?.trading.gaps.length && !picks?.gaps.length && <p>原记录未列数据缺口，不表示来源已核完整。</p>}
      </ResearchDisclosure>
    </section>
  </div>;
}
