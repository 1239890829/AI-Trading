import {currentThemeMapping, officialThemeIdentity} from "@/lib/theme-identity";
import type {ThemeCatalogIdentity, ThemeMappingState} from "@/types/market";

interface SourceThemeIdentityProps {
  reason: string;
  exact?: ThemeCatalogIdentity | null;
  state?: ThemeMappingState;
  historical?: boolean;
  notAfter?: string;
  related?: readonly {code: string; name: string; hits: number}[];
}

export function CurrentThemeReference({reason, reference, compact = false}: {reason: string; reference: unknown; compact?: boolean}) {
  const mapping=currentThemeMapping(reason,reference);
  if (!mapping) return null;
  const identity=mapping.identity;
  if (!identity) return <span className="text-[10px] text-zinc-600 dark:text-zinc-400" title="读取时的正式目录核对参考，不改写原记录或历史归属。">
    {mapping.state === "unmapped" ? "当前目录无同名对应" : mapping.state === "ambiguous" ? "当前目录同名对应不唯一" : mapping.state === "unavailable" ? "当前目录核对未就绪" : "当前标签未分类"}
  </span>;
  return <span className="inline-flex min-w-0 flex-wrap items-center gap-1 text-[10px] text-zinc-600 dark:text-zinc-400"
    title={`当前同花顺正式${identity.kind === "industry" ? "行业" : "概念"} · ${identity.name} · ${identity.code}；映射核对 ${identity.observed_at}；目录版本 ${identity.catalog_version}。不替换原标签、原指标或日期。`}>
    {compact ? "现目录同名参考" : "当前目录同名参考，非当时归属"} · {identity.code}
  </span>;
}

/** A related membership must never become an official identity or an alias. */
export function SourceThemeIdentity({reason, exact, state, historical = false, notAfter, related = []}: SourceThemeIdentityProps) {
  const official = officialThemeIdentity(reason, exact, state, notAfter);
  const unconfirmed = state === "unavailable" ? "目录未就绪 · 保留原标签" : state === "ambiguous" ? "原标签 · 目录对应不唯一"
    : state === "unclassified" ? "来源未分类" : !state && historical ? "原标签 · 当时未记录映射"
      : state === "unmapped" ? "原标签 · 无正式目录对应" : "原标签 · 映射未确认";
  return <span className="inline-flex min-w-0 flex-wrap items-center gap-1 text-[10px] text-zinc-600 dark:text-zinc-400">
    {official ? <span className="rounded bg-sky-500/10 px-1.5 py-0.5 text-sky-700 dark:text-sky-300" title={`同花顺正式${official.kind === "industry" ? "行业" : "概念"} · ${official.name} · ${official.code}；目录版本 ${official.catalog_version}；映射核对 ${official.observed_at}`}>同花顺 · {official.code}</span>
      : <span title="保留来源原始标签；未确认正式目录身份，不借用其他板块指标或成分">{unconfirmed}</span>}
    {!!related.length && <span title={related.map(item => `${item.name} ${item.code}：${item.hits}只共同成分`).join("；")}>
      {related.length}项成分重合，非身份归属
    </span>}
  </span>;
}
