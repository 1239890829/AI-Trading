"use client";

import { useExitPresence } from "@/hooks/use-exit-presence";
import {plotSample} from "@/lib/plot-sampling";
import { useMemo, useState, type ReactNode } from "react";
import { createSelectionValidationReader, type ValidationSelectionEntry, type TradeEpisode, type ValidationReport, type ValidationVersionGroup } from "@/lib/api/validation";
import {versionObservation} from "@/lib/validation-read";
import { useResource } from "@/hooks/use-resource";
import { FilterMenu } from "@/components/ui/filter-menu";
import { useSymbolDetail } from "@/components/detail/symbol-detail-context";
import { bjToday } from "@/lib/market-hours";
import { timeText, dateTimeTextBJ } from "@/lib/format";
import "./validation-panel.css";
import {ReaderBrief, ResearchDisclosure} from "@/components/ui/reader-brief";
import {readerExcerpt, readerCautions} from "@/lib/reader-copy";
import {ExecutionValidation} from "./execution-validation";
import {ValidationRecordTable} from "./validation-record-table";

const percent = (n: number | null) => n == null ? "—" : `${n > 0 ? "+" : ""}${n.toFixed(2)}%`;
const sign = (n: number | null) => n == null || n === 0 ? "flat" : n > 0 ? "up" : "down";
const price = (n: number | null) => n == null ? "未记录" : n.toFixed(2);
const stateName: Record<string, string> = {pending: "等待收盘观察", no_matching_archive: "暂无同口径结果", basis_or_window_unavailable: "结果口径待补", labeled: "已有结果", open: "持有中", closed: "已清仓", excluded: "缺项未计胜率"};

/** One chart point selects the same row as the table; no hidden data requests. */
function Chart({title, points: originalPoints, suffix, onSelect, bars = false, percentage = false}: {bars?: boolean; percentage?: boolean; title: string; points: {id: string; label: string; value: number | null}[]; suffix: string; onSelect: (id: string) => void}) {
  const [focus, setFocus] = useState<string | null>(null);
  const points = useMemo(() => bars ? originalPoints : plotSample(originalPoints), [originalPoints, bars]);
  const positions = useMemo(() => new Map(originalPoints.map((point, index) => [point, index])), [originalPoints]);
  const finite = points.filter(p => p.value != null && Number.isFinite(p.value));
  const high = percentage ? 100 : Math.max(1, ...finite.map(p => p.value!)), low = percentage ? 0 : Math.min(0, ...finite.map(p => p.value!));
  const coord = (i: number, value: number) => [48 + i * 528 / Math.max(1, originalPoints.length-1), 136 - (value-low) / (high-low || 1)*112];
  const selected = points.find(p => p.id === focus);
  return <section className="validation-chart" aria-label={title}>
    <header><h3>{title}</h3><span aria-live="polite">{selected ? `${selected.label} · ${selected.value == null ? "暂无结果" : `${selected.value.toFixed(2)}${suffix}`}` : "点击节点查看记录"}</span></header>
    {points.length < originalPoints.length && <p className="validation-muted">曲线展示 {points.length} / {originalPoints.length} 个节点，保留峰谷与原始横向位置；统计和下方记录仍为完整结果。</p>}
    {finite.length ? <svg viewBox="0 0 600 164" role="group" aria-label={`${title}，节点可用键盘选择`}>
      <line x1="48" x2="576" y1={coord(0, 0)[1]} y2={coord(0, 0)[1]} className="validation-axis" />
      <text x="0" y="18">{high.toFixed(0)}{suffix}</text><text x="0" y="136">{low.toFixed(0)}{suffix}</text>
      {points.map((p, i) => {
        if (p.value == null || !Number.isFinite(p.value)) return null;
        const [x,y] = coord(positions.get(p)!, p.value);
        const previous = i > 0 && points[i-1].value != null && Number.isFinite(points[i-1].value) ? coord(positions.get(points[i-1])!, points[i-1].value!) : null;
        return <g key={p.id}>
          {!bars && previous && <line x1={previous[0]} y1={previous[1]} x2={x} y2={y} className="validation-line" />}
          {bars && <rect x={x-Math.min(8, 200/originalPoints.length)} y={y} width={Math.min(16, 400/originalPoints.length)} height={Math.max(1, coord(0,0)[1]-y)} rx="3" className="validation-bar" />}
          <g role="button" tabIndex={0} aria-label={`${p.label} ${p.value.toFixed(2)}${suffix}，查看记录`}
            onMouseEnter={() => setFocus(p.id)} onFocus={() => setFocus(p.id)} onClick={() => onSelect(p.id)}
            onKeyDown={e => {if(e.key === "Enter" || e.key === " "){e.preventDefault(); onSelect(p.id);}}}>
            <circle cx={x} cy={y} r="14" fill="transparent" className="validation-point-target" /><circle cx={x} cy={y} r={focus === p.id ? 7 : 5} className="validation-point" data-focused={focus === p.id} />
          </g></g>;
      })}
      <text x="48" y="162">{points[0]?.label}</text><text x="576" y="162" textAnchor="end">{points.at(-1)?.label}</text>
    </svg> : <div className="validation-chart-empty">暂无可绘制结果。缺项保留，不用 0 代替。</div>}
  </section>;
}

function EntryDetail({entry}: {entry: ValidationSelectionEntry}) {
  const {open} = useSymbolDetail();
  return <div className="validation-detail">
    <div><h3>{entry.name} · 原入选记录</h3><button className="quiet-action" onClick={() => open({symbol: entry.symbol, rightTab: "book"})}>查看行情</button></div>
    <dl><div><dt>首次加入</dt><dd>{dateTimeTextBJ(entry.selected_at, {seconds:true})}</dd></div><div><dt>加入参考价</dt><dd>{price(entry.reference_price)}</dd></div><div><dt>当时涨幅</dt><dd>{percent(entry.reference_change_pct)}</dd></div><div><dt>观察天数</dt><dd>{entry.observation_calendar_days} 自然日</dd></div></dl>
    <ReaderBrief label="入选记录简述" headline={readerExcerpt(entry.reason)} cautions={[...readerCautions([entry.reason,...entry.conditions]), ...(entry.conditions.length ? ["当时附有条件，见完整依据"] : [])]}/><ResearchDisclosure identity={JSON.stringify(entry)} label="原入选完整依据"><p>{entry.reason}</p><ul>{entry.conditions.map((c,i) => <li key={i}>{c}</li>)}</ul></ResearchDisclosure>
    <p className="validation-muted">{stateName[entry.outcome_state] ?? "结果待核"} · {entry.scope === "daily" ? "每日精选" : "盘中入选"} · 入选不代表可成交。</p>
    {entry.outcome_reason && <p className="validation-muted" title={entry.outcome_reason}>结果缺口：{readerExcerpt(entry.outcome_reason, 120)}{entry.outcome_date ? ` · 目标收盘 ${entry.outcome_date}` : ""}</p>}
  </div>;
}
function EpisodeDetail({episode}: {episode: TradeEpisode}) {
  return <div className="validation-detail"><h3>{episode.name} · 成交回合</h3>
    <p>买入 {dateTimeTextBJ(episode.opened_at, {seconds:true})} → {episode.close_date ? `清仓 ${dateTimeTextBJ(episode.closed_at, {seconds:true})}` : "尚未清仓"}</p>
    <dl><div><dt>买入金额及费用</dt><dd>{(episode.cost + episode.buy_fee).toFixed(2)} 元</dd></div><div><dt>卖出净额</dt><dd>{(episode.proceeds - episode.sell_fee).toFixed(2)} 元</dd></div><div><dt>持有交易日</dt><dd>{episode.holding_trade_days ?? "日历未覆盖"}</dd></div><div><dt>流水笔数</dt><dd>{episode.fill_ids.length}</dd></div></dl>
    {episode.entry_reason && <><p>原入选摘录：{readerExcerpt(episode.entry_reason)}</p><ResearchDisclosure identity={JSON.stringify(episode)} label="成交对应的原入选依据"><p>{episode.entry_reason}</p></ResearchDisclosure></>}
    <p>{episode.issues.join("；") || "准确时刻、原入选关联与费用完整，清仓后计入胜率。"}</p>
    <p className="validation-muted">人工记录未经券商核验。加仓与部分卖出合为一回合，完全清仓后再次买入另计。</p>
  </div>;
}

type ValidationMode = "selection" | "actual" | "execution";

function ValidationBody({report, maintenance, mode, setMode, comparison}: {report: ValidationReport; maintenance: boolean;
  mode: ValidationMode; setMode: (mode: ValidationMode) => void; comparison: ReactNode}) {
  const [picked, setPicked] = useState<string | null>(null);
  const [day, setDay] = useState<string | null>(null);
  const [coverageFilter, setCoverageFilter] = useState<string | null>(null);
  const entries = report.entries.filter(e => (!day || e.date === day) && (!coverageFilter || e.coverage_state === coverageFilter));
  const expanded = useExitPresence(picked);
  const entry = report.entries.find(e => e.id === expanded.value);
  const episode = report.episodes.find(e => `episode:${e.id}` === expanded.value);
  const [methodOpen, setMethodOpen] = useState(false);
  const method = useExitPresence(methodOpen ? true : null);
  const actual = report.actual;
  const modes = <nav className="validation-modes" aria-label="统计口径">{([["selection", "入选观察"], ["actual", "实际买入"], ["execution", "执行对照"]] as const).filter(([key]) => maintenance || key !== "execution").map(([key,label]) => <button key={key} type="button" aria-pressed={mode === key} onClick={() => {setMode(key); setPicked(null); setDay(null); setCoverageFilter(null);}}>{label}</button>)}</nav>;
  if (mode === "execution") return <>{modes}<ExecutionValidation start={report.start} end={report.end}/></>;
  return <>
    {modes}
    {mode === "selection" && comparison}
    <div className="validation-overview" aria-live="polite"><div className="validation-overview-lead"><span>{mode === "selection" ? "原始入选记录" : "实际买入 · 清仓胜率"}</span><strong>{mode === "selection" ? report.selection.count : actual.win_rate == null ? "—" : `${actual.win_rate.toFixed(1)}%`}</strong><small>{mode === "selection" ? "同股跨日分别记录" : "仅统计资料齐全的清仓回合"}</small></div><div className="validation-summary">{(mode === "selection" ? [
      {label:"有效观察",value:report.selection.observed,unit:"条"},{label:"尚无有效结果",value:report.selection.missing,unit:"条"},
      {label:"平均涨跌",value:percent(report.selection.mean_pct),tone:sign(report.selection.mean_pct)},
      {label:"涨跌中位数",value:percent(report.selection.median_pct),tone:sign(report.selection.median_pct)},
    ] : [
      {label:"有效清仓回合",value:actual.denominator,unit:"回合"},{label:"持有中",value:actual.open,unit:"回合"},
      {label:"平均净涨跌",value:percent(actual.mean_pct),tone:sign(actual.mean_pct)},
      {label:"净涨跌中位数",value:percent(actual.median_pct),tone:sign(actual.median_pct)},
    ]).map(row => <div key={row.label}><span>{row.label}</span><b data-sign={row.tone}>{row.value}{row.unit && <small>{row.unit}</small>}</b></div>)}</div></div>
    <p className="validation-muted">{mode === "selection" ? `${report.basis.selection}。${report.selection.missing} 条结果未覆盖。` : `${report.basis.actual}。盈利 ${actual.wins} / 亏损 ${actual.losses} / 持平 ${actual.flat}；持有中 ${actual.open}，缺项排除 ${actual.excluded}。`}</p>
    {mode === "selection" && report.coverage && <div className="validation-coverage" aria-label="结果覆盖情况">
      {report.coverage.reasons.filter(row => row.count > 0).map(row => <button key={row.state} type="button" className="quiet-action" aria-pressed={coverageFilter === row.state} onClick={() => {setCoverageFilter(current => current === row.state ? null : row.state);setPicked(null);}}>{row.label} <b>{row.count}</b></button>)}
    </div>}
    {mode === "actual" && actual.net_expectancy_pct !== undefined && <p className="validation-muted">每个清仓回合平均净涨跌 {percent(actual.net_expectancy_pct)} · 平均盈利 / 平均亏损 {actual.payoff_ratio == null ? "样本未齐" : `${actual.payoff_ratio.toFixed(2)} 倍`}。{actual.drawdown_basis}</p>}
    <div className="validation-plots">{mode === "selection" ? <>
      <Chart bars title="每天挑选数量" suffix="只" points={report.daily.map(d => ({id:d.date, label:d.date.slice(5), value:d.count}))} onSelect={id => {setDay(id);setPicked(null);}} />
      <Chart title="入选后平均涨跌幅" suffix="%" points={report.daily.map(d => ({id:d.date, label:d.date.slice(5), value:d.mean_pct}))} onSelect={id => setDay(id)} />
    </> : <>
      <Chart percentage title="已清仓胜率曲线" suffix="%" points={report.win_curve.map(d => ({id:`episode:${d.episode_id}`, label:d.date.slice(5), value:d.win_rate}))} onSelect={setPicked} />
      <Chart title="成交净收益中位数" suffix="%" points={report.win_curve.map(d => ({id:`episode:${d.episode_id}`, label:d.date.slice(5), value:d.median_pct}))} onSelect={setPicked} />
    </>}</div>
    {mode === "actual" && <p className="validation-muted">持仓覆盖 {actual.exposure_trade_days ?? "未知"} 个交易日（同日去重）；回合累计 {actual.episode_trade_days ?? "未知"} 个交易日。旧缺项流水 {actual.legacy_fills} 笔，未混入胜率。</p>}
    <section className="validation-records"><header><h3>{mode === "selection" ? "逐笔入选" : "成交回合"}</h3>{day && <button className="quiet-action" onClick={() => setDay(null)}>清除日期筛选 {day}</button>}<span>点击行查看原依据</span></header>
      {mode === "selection" ? <ValidationRecordTable key="selections" items={entries} columns={["入选日期 / 股票","首次加入","参考价","当时涨幅","观察涨跌幅"]} resetKey={`${day ?? "all"}/${coverageFilter ?? "all"}`} label="统计明细，可横向滚动" empty={coverageFilter || day ? "当前筛选没有匹配记录，可取消筛选查看全部。" : "这个范围还没有入选记录，不用模拟记录填充。"} renderRow={e => <tr key={e.id} data-selected={picked===e.id}><td><button aria-label={`${e.name}，${e.date}入选记录`} onClick={() => setPicked(picked===e.id ? null : e.id)} aria-expanded={picked===e.id}>{e.name}<small>{e.date} · {e.symbol}</small></button></td><td>{e.selected_at ? timeText(e.selected_at) : "未记录"}</td><td>{price(e.reference_price)}</td><td data-sign={sign(e.reference_change_pct)}>{percent(e.reference_change_pct)}</td><td data-sign={sign(e.return_pct)}>{percent(e.return_pct)}</td></tr>} /> : <ValidationRecordTable key="actual" items={report.episodes} columns={["股票 / 首买日期","清仓日期","持有交易日","净收益","状态"]} resetKey="actual" label="统计明细，可横向滚动" empty="这个范围还没有实际成交流水，不用模拟记录填充。" renderRow={e => <tr key={e.id} data-selected={picked===`episode:${e.id}`}><td><button onClick={() => setPicked(picked===`episode:${e.id}` ? null : `episode:${e.id}`)} aria-expanded={picked===`episode:${e.id}`}>{e.name}<small>{e.open_date} · {e.symbol}</small></button></td><td>{e.close_date ?? "持有中"}</td><td>{e.holding_trade_days ?? "未知"}</td><td data-sign={sign(e.return_pct)}>{percent(e.return_pct)}</td><td>{stateName[e.state]}</td></tr>} />}
      {expanded.value && <div className="validation-expanded" data-open={expanded.active} inert={!expanded.active} aria-hidden={!expanded.active}><div>{entry && <EntryDetail key={entry.id} entry={entry} />}{episode && <EpisodeDetail key={episode.id} episode={episode} />}</div></div>}
    </section>
    {mode === "actual" && report.cohorts.length > 0 && <section className="validation-cohorts"><h3>原入选依据：成功与失败对照</h3><p className="validation-muted">按成交前记录的依据分组，只作描述；样本少不能认定因果，也不自动改策略。</p>{report.cohorts.map((c,i) => <div key={i}><p>{readerExcerpt(c.reason)}</p><ResearchDisclosure label="分组原依据" identity={`${i}:${c.reason}`}><p>{c.reason}</p></ResearchDisclosure><span>有效 {c.denominator} / 总回合 {c.total} · 盈 {c.wins} / 亏 {c.losses} · 中位 {percent(c.median_pct)}</span></div>)}</section>}
    {mode === "selection" && report.coverage && <ResearchDisclosure label="方法版本与样本覆盖" identity={JSON.stringify(report.coverage)}>
      <p>{report.coverage.basis}</p><ul>{report.coverage.versions.map((group,index) => <li key={index}>{group.scope === "daily" ? "每日精选" : "盘中入选"} · {group.method_version || "原方法版本未记录"} / {group.feature_version || "原特征版本未记录"}：{group.observed}/{group.count} 条有效结果，覆盖 {group.observed_days} 个入选日；平均 {percent(group.mean_pct)}，中位 {percent(group.median_pct)}</li>)}</ul>
      {!!report.coverage.strata?.length && <><h4>原入选时段、形态与环境</h4><p>仅使用当时留存的分类，未记录的不推断；不同方法版本分开显示。</p><ul>{report.coverage.strata.map((group,index) => <li key={index}>{({entry_time:"时段",shape:"形态",environment:"环境"} as Record<string,string>)[group.dimension]} · {group.label} · {group.scope === "daily" ? "每日" : "盘中"} / {group.method_version || "版本缺失"}：{group.observed}/{group.count} 条有效，{group.observed_days} 个入选日；平均 {percent(group.mean_pct)}，中位 {percent(group.median_pct)}</li>)}</ul></>}
    </ResearchDisclosure>}
    <section className="validation-method"><button type="button" aria-expanded={methodOpen} onClick={() => setMethodOpen(open => !open)}>如何读结果、怎样改进</button>{method.value && <div className="validation-expanded" data-open={method.active} inert={!method.active} aria-hidden={!method.active}><div><p>{report.basis.coverage}</p><p>入选数量来自现存档案。旧每日覆盖式存储无法还原全部轮次；没有记录的历史不补造。</p><p>先看同一入选时点的原依据，再比较盈利与亏损的样本。小样本、未成交、缺费用或缺源价不能证明战法有效。把握主升浪与缩窄范围须经过分时期的前向对照，不能从赢家倒推。</p><p>{report.basis.improvement}</p></div></div>}</section>
    <p className="validation-disclaimer">仅供投研与复盘，不构成买卖建议。</p>
  </>;
}

type VersionChoice = {context: string; a: string; b: string; view: "all" | "a" | "b"};
function versionLabel(group: ValidationVersionGroup) {
  return `${group.scope === "daily" ? "每日" : "盘中"} · ${group.method_version || "方法未记录"} / ${group.feature_version || "特征未记录"}`;
}

export function ValidationContent({report, maintenance = false}: {report: ValidationReport; maintenance?: boolean}) {
  const context = `${report.start}/${report.end}/${report.scope}/${report.horizon}`;
  const [mode, setMode] = useState<ValidationMode>("selection");
  const [choice, setChoice] = useState<VersionChoice | null>(null);
  const groups = report.version_groups ?? [];
  const current = choice?.context === context ? choice : null;
  const a = current ? groups.find(group => group.key === current.a) : groups[0];
  const b = current ? groups.find(group => group.key === current.b) : groups[1];
  const requested = current?.view === "a" ? a : current?.view === "b" ? b : undefined;
  const projected = requested ? versionObservation(report, requested.key) : null;
  const active = mode === "selection" && projected ? current?.view || "all" : "all";
  const shown = active !== "all" && projected ? projected : report;
  function choose(update: Partial<Omit<VersionChoice, "context">>) {
    setChoice({context, a: a?.key || "", b: b?.key || "", view: current?.view || "all", ...update});
  }
  const differentWindows = a && b && JSON.stringify(a.window) !== JSON.stringify(b.window);
  const comparison = groups.length > 0 ? <section className="validation-versions" aria-label="版本效果对照">
    <header><h3>原版本对照</h3><button type="button" className="quiet-action" aria-pressed={active === "all"} onClick={() => choose({view:"all"})}>全部原入选</button></header>
    <div className="validation-version-rows">{([{slot:"a", group:a}, {slot:"b", group:b}] as const).map(({slot,group}) => <div key={slot} className="validation-version-row" data-active={active === slot}>
      <div className="validation-version-choice"><button type="button" className="quiet-action" aria-pressed={active === slot} disabled={!group} onClick={() => choose({view:slot})}>查看 {slot.toUpperCase()}</button>
        <label><span className="sr-only">版本 {slot.toUpperCase()}</span><select aria-label={`版本 ${slot.toUpperCase()}`} value={group?.key || ""} onChange={event => choose({[slot]:event.target.value})}>
          <option value="">请选择原版本</option>{groups.map(option => <option key={option.key} value={option.key}>{versionLabel(option)}</option>)}
        </select></label></div>
      {group ? <><dl aria-label={`${slot.toUpperCase()} 原观察摘要`}>
        <div><dt>原记录</dt><dd>{group.selection.count}</dd></div><div><dt>有效</dt><dd>{group.selection.observed}</dd></div>
        <div><dt>均值</dt><dd data-sign={sign(group.selection.mean_pct)}>{percent(group.selection.mean_pct)}</dd></div>
        <div><dt>中位</dt><dd data-sign={sign(group.selection.median_pct)}>{percent(group.selection.median_pct)}</dd></div>
      </dl><p className="validation-muted">{group.window.observed_start ? `有效入选日 ${group.window.observed_start} → ${group.window.observed_end} · ${group.window.observed_days} 日` : "尚无有效结果；不按零收益补齐"}{group.version_state === "missing" ? " · 原版本未完整记录，不能视为可比新版本" : ""}</p></>
        : <p className="validation-muted">当前范围暂无另一组原版本，可调整日期或来源。</p>}
    </div>)}</div>
    <p className="validation-muted" aria-live="polite">当前查看：{active === "all" ? "全部原入选，同股按日去重" : `${active.toUpperCase()} · ${requested ? versionLabel(requested) : ""}`}。{differentWindows ? "两组实际覆盖日不同；" : ""}仅为已存观察，不证明版本因果或成交效果。</p>
    {current && ((current.view === "a" && !a) || (current.view === "b" && !b)) && <p role="status" className="validation-muted">所选版本已不在当前范围，已恢复全部原入选。</p>}
    {requested && !projected && <p role="alert" className="validation-muted">该版本原记录不完整，当前保留全部视图；请刷新后核对。</p>}
  </section> : null;
  return <ValidationBody key={`${context}/${mode}/${active === "all" ? "all" : requested?.key}`} report={shown} maintenance={maintenance} mode={mode} setMode={setMode} comparison={comparison}/>;
}

export function SelectionValidationPanel({maintenance = false}: {maintenance?: boolean} = {}) {
  const [end, setEnd] = useState(bjToday);
  const [start, setStart] = useState(() => {const t = new Date(`${bjToday()}T12:00:00+08:00`);t.setUTCDate(t.getUTCDate()-29);return t.toISOString().slice(0,10);});
  const [horizon, setHorizon] = useState("d1_close"), [scope, setScope] = useState("all");
  const key = `${start}/${end}/${horizon}/${scope}`;
  const load = useMemo(() => createSelectionValidationReader(start,end,horizon,scope),[start,end,horizon,scope]);
  const resource = useResource(load, {key, intervalMs: 60_000, abortable: true});
  return <div className="selection-validation"><header className="validation-heading"><div><h2>验证统计</h2><p>挑选得如何，买入后又如何，分开核对。</p></div><button className="quiet-action" disabled={resource.pending} onClick={resource.refresh}>{resource.pending ? "读取中…" : "刷新统计"}</button></header>
    <div className="validation-filters"><label>起始日期<input aria-label="起始日期" type="date" value={start} max={end} onChange={e => {if(e.target.value)setStart(e.target.value);}} /></label><label>截止日期<input aria-label="截止日期" type="date" value={end} min={start} max={bjToday()} onChange={e => {if(e.target.value)setEnd(e.target.value);}} /></label>
      <FilterMenu label="入选来源" value={scope} options={[{key:"all",label:"同股按日去重"},{key:"daily",label:"每日精选"},{key:"intraday",label:"盘中入选"}]} onChange={setScope} />
      <FilterMenu label="观察周期" value={horizon} options={[{key:"d0_close",label:"当日收盘"},{key:"d1_close",label:"下一交易日"},{key:"d3_close",label:"第3交易日"},{key:"d5_close",label:"第5交易日"}]} onChange={setHorizon} />
    </div>
    <p className="validation-muted">入选来源与观察周期只影响入选观察；实际买入按清仓日统计。</p>
    {!!resource.error && <p role="alert">统计读取失败，请重试。{String(resource.error)}</p>}
    {resource.pending && <p role="status">正在读取原入选与成交流水…</p>}
    {resource.data && <ValidationContent key={key} report={resource.data} maintenance={maintenance} />}
  </div>;
}
