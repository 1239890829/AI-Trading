interface SourceThemeIdentityProps {
  reason: string;
  exact?: {code: string; name: string; source: string; basis: string} | null;
  related?: readonly {code: string; name: string; hits: number}[];
}

/** A related membership must never become an official identity or an alias. */
export function SourceThemeIdentity({reason, exact, related = []}: SourceThemeIdentityProps) {
  const official = exact?.source === "ths_official" && exact.basis === "exact_name"
    && /^\d{6}\.TI$/.test(exact.code) && exact.name === reason;
  return <span className="inline-flex min-w-0 flex-wrap items-center gap-1 text-[10px] text-zinc-600 dark:text-zinc-400">
    {official ? <span className="rounded bg-sky-500/10 px-1.5 py-0.5 text-sky-700 dark:text-sky-300" title={`同花顺正式概念 · ${exact.name} · ${exact.code}`}>同花顺 · {exact.code}</span>
      : <span title="保留数据源原始原因；未确认正式概念代码，未用相近名称替代">来源原因 · 正式概念未映射</span>}
    {!!related.length && <span title={related.map(item => `${item.name} ${item.code}：${item.hits}只共同成分`).join("；")}>
      {related.length}项成分重合，非身份归属
    </span>}
  </span>;
}
