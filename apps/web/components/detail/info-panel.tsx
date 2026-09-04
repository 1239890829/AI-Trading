/** 资讯页签：近期公告 + 相关新闻，附规则摘要（重要度/消息面情绪/事实摘要）。

摘要字段全部可选——兼容未走 /api/news/digest 的纯列表数据。
重要：摘要只做事实抽取，不得出现任何买卖建议（AGENTS.md 红线 3）。 */
import { useState } from "react";

import { NewsModal, type NewsModalItem } from "@/components/news-modal";

export interface InfoItem {
  title: string;
  date: string;
  url: string;
  type?: string | null;
  summary?: string | null;
  source: string;

  // ---- 规则摘要（来自 /api/news/digest，可选）----
  importance?: "高" | "中" | "普通" | "低";
  importance_score?: number;
  importance_reasons?: string[];
  sentiment?: "偏正面" | "偏负面" | "分歧" | "中性";
  sentiment_reasons?: string[];
  digest?: string;
  digest_source?: string;
  numbers?: string[];
}

const IMPORTANCE_STYLE: Record<string, string> = {
  高: "bg-up/15 text-up",
  中: "bg-amber-500/15 text-amber-400",
  普通: "bg-zinc-500/15 text-zinc-400",
  低: "bg-zinc-500/10 text-zinc-500",
};

const SENTIMENT_STYLE: Record<string, string> = {
  偏正面: "text-up",
  偏负面: "text-down",
  分歧: "text-amber-400",
  中性: "text-zinc-400",
};

function DigestRow({ item }: { item: InfoItem }) {
  if (!item.digest && item.importance === undefined) return null;
  return (
    <div className="mt-1 space-y-0.5">
      <div className="flex items-center gap-1.5">
        {item.importance && (
          <span
            className={`rounded px-1 py-px text-[10px] font-medium ${IMPORTANCE_STYLE[item.importance] ?? ""}`}
            title={
              item.importance_reasons?.length
                ? `重要度依据：${item.importance_reasons.join("、")}`
                : "未命中关键词，按默认重要度"
            }
          >
            {item.importance}
          </span>
        )}
        {item.sentiment && (
          <span
            className={`text-[10px] ${SENTIMENT_STYLE[item.sentiment] ?? "text-zinc-400"}`}
            title={
              item.sentiment_reasons?.length
                ? `情绪依据：${item.sentiment_reasons.join("、")}`
                : "未命中正负面词"
            }
          >
            {item.sentiment}
          </span>
        )}
        {item.numbers && item.numbers.length > 0 && (
          <span className="truncate font-mono text-[10px] text-zinc-500" title={item.numbers.join(" ")}>
            {item.numbers.slice(0, 3).join(" ")}
          </span>
        )}
      </div>
      {item.digest && (
        // digest_source 说明摘要取自正文还是标题（表格型正文会被丢弃）
        <p className="line-clamp-2 text-[11px] leading-snug text-zinc-400" title={`${item.digest}（来源：${item.digest_source ?? "未知"}）`}>
          {item.digest}
        </p>
      )}
    </div>
  );
}

export function InfoPanel({ anns, news }: { anns: InfoItem[] | null; news: InfoItem[] | null }) {
  const [modalItem, setModalItem] = useState<NewsModalItem | null>(null);
  const openModal = (item: InfoItem, kindLabel: string) =>
    setModalItem({ title: item.title, url: item.url, date: item.date, source: item.source, digest: item.digest ?? null, kindLabel });
  return (
    <div className="min-h-0 overflow-y-auto">
      <h3 className="px-3 py-1.5 text-xs font-medium text-zinc-500 dark:text-zinc-300">近期公告</h3>
      {(anns ?? []).map((a, i) => (
        <button
          key={i}
          onClick={() => openModal(a, "公告")}
          className="block w-full border-b border-zinc-100 px-3 py-1.5 text-left hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900"
        >
          <div className="truncate text-xs text-zinc-900 dark:text-zinc-200">{a.title}</div>
          <div className="text-[11px] text-zinc-500">
            {a.date} {a.type ? `· ${a.type}` : ""}
          </div>
          <DigestRow item={a} />
        </button>
      ))}
      {anns && anns.length === 0 && <p className="px-3 py-3 text-xs text-zinc-500">暂无公告</p>}
      <h3 className="border-t border-zinc-100 px-3 py-1.5 text-xs font-medium text-zinc-300 dark:border-zinc-800/60">相关新闻</h3>
      {(news ?? []).map((n, i) => (
        <button
          key={i}
          onClick={() => openModal(n, "新闻")}
          className="block w-full border-b border-zinc-100 px-3 py-1.5 text-left hover:bg-zinc-50 dark:border-zinc-800/60 dark:hover:bg-zinc-900"
        >
          <div className="truncate text-xs text-zinc-900 dark:text-zinc-200">{n.title}</div>
          <div className="text-[11px] text-zinc-500">{n.date}</div>
          <DigestRow item={n} />
        </button>
      ))}
      {news && news.length === 0 && <p className="px-3 py-3 text-xs text-zinc-500">暂无新闻</p>}
      <NewsModal item={modalItem} onClose={() => setModalItem(null)} />
    </div>
  );
}
