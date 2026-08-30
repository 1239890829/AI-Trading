/** 资讯页签：近期公告 + 相关新闻。纯展示。 */
export interface InfoItem {
  title: string;
  date: string;
  url: string;
  type?: string | null;
  summary?: string | null;
  source: string;
}

export function InfoPanel({ anns, news }: { anns: InfoItem[] | null; news: InfoItem[] | null }) {
  return (
    <div className="min-h-0 overflow-y-auto">
      <h3 className="px-3 py-1.5 text-xs font-medium text-zinc-300">近期公告</h3>
      {(anns ?? []).map((a, i) => (
        <a
          key={i}
          href={a.url}
          target="_blank"
          rel="noreferrer"
          className="block border-b border-zinc-100 px-3 py-1.5 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900"
        >
          <div className="truncate text-xs text-zinc-200">{a.title}</div>
          <div className="text-[11px] text-zinc-500">
            {a.date} {a.type ? `· ${a.type}` : ""}
          </div>
        </a>
      ))}
      {anns && anns.length === 0 && <p className="px-3 py-3 text-xs text-zinc-500">暂无公告</p>}
      <h3 className="border-t border-zinc-100 px-3 py-1.5 text-xs font-medium text-zinc-300 dark:border-zinc-800/60">相关新闻</h3>
      {(news ?? []).map((n, i) => (
        <a
          key={i}
          href={n.url}
          target="_blank"
          rel="noreferrer"
          className="block border-b border-zinc-100 px-3 py-1.5 hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900"
        >
          <div className="truncate text-xs text-zinc-200">{n.title}</div>
          <div className="text-[11px] text-zinc-500">{n.date}</div>
        </a>
      ))}
      {news && news.length === 0 && <p className="px-3 py-3 text-xs text-zinc-500">暂无新闻</p>}
    </div>
  );
}
