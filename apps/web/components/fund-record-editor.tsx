"use client";

import {useRef, useState} from "react";
import {ApiError} from "@/lib/api/client";
import {deleteAssetRecord, saveAssetRecord, type AssetInstrument, type AssetRecord, type FundNavPoint} from "@/lib/api/assets";
import {notifyAssetRecordsChanged} from "@/lib/asset-identity";

function amount(value: number | null | undefined) {return value == null || !Number.isFinite(value) ? "--" : value.toLocaleString("zh-CN", {maximumFractionDigits: 2});}

export function FundRecordEditor({instrument, record, latest, loading, onRefresh}: {
  instrument: AssetInstrument; record?: AssetRecord; latest: FundNavPoint | null; loading: boolean; onRefresh: () => void;
}) {
  const [editing, setEditing] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [error, setError] = useState("");
  const [note, setNote] = useState("");
  const units = record?.units;
  const value = units != null && latest?.unit_nav != null ? units * latest.unit_nav : null;
  const pnl = value !== null && record?.cost_total != null ? value - record.cost_total : null;
  async function toggleWatch() {
    if (lock.current || loading) return;
    lock.current = true; setBusy(true); setError(""); setNote("");
    try {
      if (record?.is_watched && record.units == null) {
        await deleteAssetRecord(record.instrument_id, record.version);
      } else {
        await saveAssetRecord(instrument.instrument_id, {source_symbol: instrument.source_symbol, name: record?.name ?? (instrument.name.length <= 80 ? instrument.name : null),
          is_watched: !record?.is_watched, units: record?.units ?? null, cost_total: record?.cost_total ?? null,
          note: record?.note ?? null, expected_version: record?.version ?? 0});
      }
      notifyAssetRecordsChanged(); onRefresh(); setNote(record?.is_watched ? record.units == null ? "已移出自选" : "已移出自选，持有记录保留" : "已加入基金自选");
    } catch (e) {setError(e instanceof ApiError && e.status === 409 ? "记录已有新版本。请重新读取后再操作。" : `保存失败：${e instanceof Error ? e.message : "请重试"}`);}
    finally {lock.current = false; setBusy(false);}
  }
  async function remove() {
    if (!record || lock.current || loading) return;
    lock.current = true; setBusy(true); setError("");
    try {await deleteAssetRecord(record.instrument_id, record.version); notifyAssetRecordsChanged(); onRefresh(); setConfirmDelete(false); setNote("手工记录已删除");}
    catch (e) {setError(`删除失败：${e instanceof Error ? e.message : "请重试"}`);}
    finally {lock.current = false; setBusy(false);}
  }
  return <section className="asset-record" aria-label="基金持有记录">
    <div className="asset-section-heading"><div><h3>我的记录</h3><span>手工记录 · 未经券商核验 · 不执行交易</span></div><div className="asset-actions">
      <button className="bc-compact-button" disabled={busy || loading} onClick={() => void toggleWatch()}>{busy ? "保存中…" : record?.is_watched ? "移出自选" : "加入自选"}</button>
      <button className="bc-compact-button" disabled={busy || loading} onClick={() => {setEditing(!editing); setError(""); setNote("");}}>{editing ? "取消编辑" : record?.units != null ? "编辑持有" : "记录持有"}</button>
    </div></div>
    {record?.units != null && <dl className="asset-facts asset-record-facts">
      <div><dt>持有份额</dt><dd>{amount(units)}</dd></div><div><dt>总成本（{instrument.currency}）</dt><dd>{amount(record.cost_total)}</dd></div>
      <div><dt>按净值记录市值</dt><dd>{amount(value)} <small>{instrument.currency}</small></dd></div>
      <div><dt>账面差额</dt><dd className={pnl == null ? "" : pnl >= 0 ? "text-up-ink dark:text-up" : "text-down-ink dark:text-down"}>{amount(pnl)} <small>{instrument.currency}</small></dd></div>
    </dl>}
    {record?.units != null && <p className="asset-note">{latest?.unit_nav != null ? `按 ${latest.nav_date} 官方单位净值计算，未含未记录的分红、费用或份额变动；不是实时估值。` : "缺少官方单位净值，未计算市值和账面差额。"}</p>}
    {record?.note && !editing && <p className="asset-note">备注：{record.note}</p>}
    {editing && <RecordForm instrument={instrument} record={record} onSaved={() => {setEditing(false); notifyAssetRecordsChanged(); onRefresh(); setNote("已保存手工持有记录");}} onRefresh={onRefresh} />}
    {error && <p role="alert" className="asset-error">{error} <button className="bc-compact-button" onClick={onRefresh}>重新读取</button></p>}
    {note && <p role="status" className="asset-note">{note}</p>}
    {record && !editing && <div className="asset-delete-record">{confirmDelete ? <><span>删除自选及持有记录？</span><button className="bc-compact-button bc-danger-button" disabled={busy || loading} onClick={() => void remove()}>确认删除记录</button><button className="bc-compact-button" disabled={busy} onClick={() => setConfirmDelete(false)}>取消</button></> : <button className="asset-text-button" onClick={() => setConfirmDelete(true)}>删除记录</button>}</div>}
  </section>;
}

function RecordForm({instrument, record, onSaved, onRefresh}: {instrument: AssetInstrument; record?: AssetRecord; onSaved: () => void; onRefresh: () => void}) {
  const [units, setUnits] = useState(record?.units?.toString() ?? "");
  const [cost, setCost] = useState(record?.cost_total?.toString() ?? "");
  const [note, setNote] = useState(record?.note ?? "");
  // Freeze the form's original version. A concurrent refresh cannot authorize an overwrite.
  const [version] = useState(record?.version ?? 0);
  const [watched] = useState(record?.is_watched ?? true);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const [error, setError] = useState("");
  async function save(event: React.FormEvent) {
    event.preventDefault(); if (lock.current) return;
    const quantity = Number(units), total = Number(cost);
    if (!units.trim() || !cost.trim() || !Number.isFinite(quantity) || !Number.isFinite(total) || quantity < 0 || total < 0 || quantity === 0 && total !== 0) {
      setError("请输入有效的份额与同币种总成本；清仓时两项均填 0。"); return;
    }
    lock.current = true; setBusy(true); setError("");
    try {
      await saveAssetRecord(instrument.instrument_id, {source_symbol: instrument.source_symbol, name: record?.name ?? (instrument.name.length <= 80 ? instrument.name : null),
        is_watched: watched, units: quantity, cost_total: total, note: note.trim() || null, expected_version: version});
      onSaved();
    } catch (e) {
      setError(e instanceof ApiError && e.status === 409 ? "记录已被其他操作更新。输入已保留；请取消编辑、重新读取，再核对最新记录。" : `保存失败，输入已保留：${e instanceof Error ? e.message : "请重试"}`);
    } finally {lock.current = false; setBusy(false);}
  }
  return <form className="asset-record-form" onSubmit={event => void save(event)}>
    <label>持有份额<input inputMode="decimal" value={units} onChange={event => setUnits(event.target.value)} disabled={busy} /></label>
    <label>总成本（{instrument.currency}）<input inputMode="decimal" value={cost} onChange={event => setCost(event.target.value)} disabled={busy} /></label>
    <label className="asset-record-note">备注<input value={note} onChange={event => setNote(event.target.value)} maxLength={300} disabled={busy} /></label>
    <div className="asset-actions"><button className="bc-compact-button" type="submit" disabled={busy}>{busy ? "保存中…" : "保存持有记录"}</button><span className="asset-note">不换汇，不合并不同币种。</span></div>
    {error && <p role="alert" className="asset-error">{error} <button className="bc-compact-button" type="button" onClick={onRefresh}>重新读取记录</button></p>}
  </form>;
}
