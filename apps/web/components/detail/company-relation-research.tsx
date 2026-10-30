"use client";

import {useEffect, useId, useRef, useState} from "react";
import {useDeviceAccess} from "@/components/access/device-access";
import {useResource} from "@/hooks/use-resource";
import {createAgentTask, type AgentTask} from "@/lib/api/agent";
import {ApiError} from "@/lib/api/client";
import {companyRelationKey, getCompanyRelationResearch, validCompanyRelationIdentity,
  type CompanyRelationChoice, type CompanyRelationIdentity, type CompanyRelationItem} from "@/lib/api/company-decisions";
import {dateTimeTextBJ} from "@/lib/format";

const LABEL: Record<CompanyRelationChoice | "refusal", string> = {
  supported: "支持关联", contradicted: "反驳关联", insufficient: "证据不够", unrelated: "无关", refusal: "拒答",
};
const KIND = {business: "主营", product: "产品", customer: "客户", order: "订单", risk: "风险"};
const STATUS: Record<string, string> = {
  not_requested: "尚无本版本核验记录", not_started: "上次未启动", reserved: "任务已预留，尚未完成",
  started: "核验执行中", interrupted: "原任务已中断，未重复生成", accounting_pending: "答案已保留，待核对保存与用量",
  stale: "材料或有效期已变化，旧判断不作当前依据", historical_excluded: "当前结果不回填历史",
  unavailable: "已有材料暂不满足核验条件", invalid_receipt: "保存记录未通过身份核对", ready: "已保存 · 研究待核",
};
const REASON: Record<string, string> = {
  disabled: "关联核验尚未启用", not_configured: "研究通道尚未配置",
  invalid_proxy: "研究通道配置待核", invalid_timeout: "研究通道配置待核",
  company_evidence_missing: "尚无已保存的公司原文", company_evidence_unavailable: "公司原文尚未就绪",
  company_evidence_stale: "公司原文已过有效期", company_evidence_invalid: "公司原文未通过完整性核对",
  event_unavailable_or_changed: "事件已变化，请重新读取事件", source_time_invalid: "来源时间待核",
  usage_unknown: "当前用量记录待核，暂不能发起新研究",
};

/** A current, source-bound stock event opts in. No mount/visibility/model polling. */
export function CompanyRelationResearch({identity}: {identity: CompanyRelationIdentity}) {
  if (!validCompanyRelationIdentity(identity)) return null;
  return <RelationReader key={companyRelationKey(identity)} identity={identity}/>;
}

function RelationReader({identity}: {identity: CompanyRelationIdentity}) {
  const access = useDeviceAccess();
  const canMaintain = access.device?.state === "active" && access.device.maintenance === true;
  const [open, setOpen] = useState(false);
  const [requested, setRequested] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState<AgentTask | null>(null);
  const [submitError, setSubmitError] = useState<{message: string; uncertain: boolean} | null>(null);
  const [filter, setFilter] = useState<CompanyRelationChoice | "refusal" | null>(null);
  const [now, setNow] = useState(0);
  const id = useId();
  const alive = useRef(true), busy = useRef(false);
  useEffect(() => {alive.current = true; return () => {alive.current = false;};}, []);
  const resource = useResource(signal => getCompanyRelationResearch(identity, signal), {
    key: companyRelationKey(identity), enabled: requested, intervalMs: null,
    marketHours: false, refreshOnVisible: false, abortable: true,
  });
  const view = resource.data;
  const expiresAt = view?.result?.valid_until;
  useEffect(() => {
    if (!expiresAt) return;
    const timer = setTimeout(() => setNow(Date.now()), Math.max(0, Math.min(Date.parse(expiresAt) - Date.now(), 2147483647)));
    return () => clearTimeout(timer);
  }, [expiresAt]);
  const expired = !!expiresAt && Date.parse(expiresAt) <= now;
  const uncertain = submitError?.uncertain === true;
  const waitingReceipt = !!submitted && submitted.id !== view?.task_id;
  const validResult = !resource.error && !waitingReceipt && !expired && view?.state === "ready" ? view.result : null;
  const canRequest = canMaintain && view?.can_request === true && !validResult && !resource.pending
    && !resource.error && !submitting && !uncertain && !submitted;
  const items = validResult?.items || [];
  const counts = items.reduce((all, item) => {
    const kind = item.type === "refusal" ? "refusal" : item.choice!;
    all[kind] = (all[kind] || 0) + 1;
    return all;
  }, {} as Partial<Record<CompanyRelationChoice | "refusal", number>>);
  const filtered = filter ? items.filter(item => (item.type === "refusal" ? "refusal" : item.choice) === filter) : items;

  async function generate() {
    if (!canRequest || busy.current) return;
    busy.current = true;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const task = await createAgentTask("company_relation_research", {...identity});
      if (!alive.current) return;
      if (!task || !/^[a-f0-9]{32}$/.test(task.id) || task.type !== "company_relation_research"
        || !task.params || Object.keys(task.params).length !== 4
        || Object.entries(identity).some(([key, value]) => task.params[key] !== value)
        || !["queued", "running", "succeeded", "failed", "canceled"].includes(task.status)) {
        throw new Error("任务回执身份不匹配，请到任务中心核对");
      }
      setSubmitted(task);
    } catch (error) {
      if (alive.current) setSubmitError({message: error instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(error.status)
        ? "请求未获准，请核对资料与维护权限。" : "提交结果未确认；请先到任务中心核对，勿重复生成。",
      uncertain: !(error instanceof ApiError && [400, 401, 403, 404, 409, 422].includes(error.status))});
    } finally {
      busy.current = false;
      if (alive.current) setSubmitting(false);
    }
  }

  return <section aria-label="公司关联核验" className="min-w-0 border-t border-zinc-200 py-2 text-xs dark:border-zinc-800">
    <button type="button" data-action="quiet" aria-expanded={open} aria-controls={id} onClick={() => {
      if (!requested) setRequested(true);
      setOpen(value => !value);
    }} className="inline-flex min-h-8 items-center gap-1.5">
      <svg aria-hidden="true" width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5"><path d="m9 12 2 2 4-4"/><path d="M12 3 4 6v6c0 5 8 9 8 9s8-4 8-9V6z"/></svg>
      {open ? "收起关联核验" : "读取关联核验"}
    </button>
    <div id={id} hidden={!open} className="min-w-0 space-y-2 pt-1">
      <div className="flex flex-wrap items-center gap-2">
        <span role="status">{resource.error ? "读取失败，当前状态未确认" : resource.pending ? "正在读取保存记录…" : waitingReceipt ? "本次任务结果尚未对应" : expired ? STATUS.stale : view ? STATUS[view.state] : "尚未读取"}</span>
        <button type="button" data-action="secondary" disabled={!requested || resource.pending || submitting}
          onClick={() => {setFilter(null); resource.refresh();}}>重新读取</button>
        {canMaintain && <button type="button" data-action="command" disabled={!canRequest}
          onClick={() => void generate()}>{submitting ? "正在提交…" : "请求关联核验"}</button>}
      </div>
      {view?.reason_code && REASON[view.reason_code] && <p className="text-zinc-600 dark:text-zinc-400">{REASON[view.reason_code]}</p>}
      <p className="text-zinc-600 dark:text-zinc-400">核对事件与公司原文的关系；未验证准确率，不改变入选或模拟资格。</p>
      {resource.error != null && <p role="alert" className="text-amber-800 dark:text-amber-300">请重新读取；不会自动生成或重试模型。</p>}
      {submitError && <p role="alert" className="text-amber-800 dark:text-amber-300">{submitError.message}</p>}
      {submitted && <p role="status">{waitingReceipt ? "任务已提交，请稍后重新读取；尚未确认保存结果。" : "任务身份已对应；是否完成以保存结果为准。"}</p>}
      {(submitted || uncertain || canMaintain && view && !view.can_request && view.state !== "ready")
        && <a href="/agent?area=maintenance&tab=tasks" data-action="quiet">任务中心核对</a>}
      {!canMaintain && view?.state !== "ready" && <p className="text-zinc-600 dark:text-zinc-400">生成需维护权限；已有结果可直接读。<button type="button" data-action="quiet" className="ml-1" onClick={access.open}>设备与权限</button></p>}
      {validResult && <>
        <div className="flex flex-wrap items-center gap-1" aria-label="核验关系筛选">
          <button type="button" data-action="secondary" aria-pressed={filter === null} onClick={() => setFilter(null)}>全部 {items.length}</button>
          {(Object.keys(LABEL) as (keyof typeof LABEL)[]).filter(key => counts[key]).map(key =>
            <button key={key} type="button" data-action="secondary" aria-pressed={filter === key} onClick={() => setFilter(key)}>
              {LABEL[key]} <span className="tabular-nums">{counts[key]}</span>
            </button>)}
        </div>
        <ul className="min-w-0 divide-y divide-zinc-200 dark:divide-zinc-800" aria-label="逐条公司依据核验">
          {filtered.map(item => <EvidenceRow key={item.fact_id} item={item}/>)}
        </ul>
        <p className="text-zinc-600 dark:text-zinc-400">保存于 {dateTimeTextBJ(validResult.generated_at)} · 可答 {validResult.coverage.answered}/{validResult.coverage.total} 条</p>
      </>}
    </div>
  </section>;
}

function EvidenceRow({item}: {item: CompanyRelationItem}) {
  const result = item.type === "refusal" ? "refusal" : item.choice!;
  return <li className="min-w-0 py-2">
    <details className="min-w-0">
      <summary className="cursor-pointer [overflow-wrap:anywhere]">
        <span className={`mr-2 font-medium ${result === "contradicted" ? "text-amber-800 dark:text-amber-300" : ""}`}>{LABEL[result]}</span>
        <span className="mr-2">{KIND[item.fact.kind]}</span>
        <span className="text-zinc-600 dark:text-zinc-400">{item.fact.document_title} · 第{item.fact.page}页</span>
      </summary>
      <blockquote className="py-2 leading-relaxed [overflow-wrap:anywhere]">{item.fact.quote}</blockquote>
      <a href={item.fact.url} target="_blank" rel="noopener noreferrer" className="underline underline-offset-2">查看披露原文</a>
    </details>
  </li>;
}
