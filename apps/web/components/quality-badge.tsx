import { shouldShowQualityBadge } from "@/lib/format";
import { quoteQualityView, type QuoteQualityInput } from "@/lib/quote-quality-view";

const TONES = {
  quiet: "text-zinc-600 dark:text-zinc-400",
  warning: "text-orange-800 dark:text-orange-400",
  danger: "text-red-700 dark:text-red-400",
};

/** Keep one stable slot while reporting actual source quality without a debounce. */
export function QualityBadge(props: QuoteQualityInput & {compact?: boolean; align?: "start" | "center"; notice?: {label: string; title?: string}}) {
  const view = quoteQualityView(props);
  if (!props.quality && !view.sessionLabel) return null;
  if (view.sessionLabel) {
    const notice = [...new Set([view.sessionIssue?.label, ...(props.notice?.label.split("；") ?? [])].filter(Boolean))].join("；");
    const title = [view.title, props.notice?.title].filter(Boolean).join("；") || undefined;
    return <span data-quality={props.quality} title={title} className={`inline-flex w-[6.4em] max-w-full shrink-0 items-center gap-1 whitespace-nowrap rounded py-0.5 ${props.align === "start" ? "justify-start px-0" : `justify-center ${props.compact ? "px-1" : "px-1.5"}`} ${props.compact ? "text-[10px]" : "text-xs"} ${TONES.quiet}`}>
      <span data-session-label data-quality={props.quality} title={title}>{view.sessionLabel}</span>
      {notice && <span role="img" aria-label={`行情提示：${notice}`} data-quality={props.quality} title={title} className={`inline-flex shrink-0 ${props.quality === "invalid" ? TONES.danger : TONES.warning}`}><svg aria-hidden="true" width="12" height="12" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.6"><circle cx="8" cy="8" r="6"/><path d="M8 4.5v4M8 10.5v1"/></svg></span>}
    </span>;
  }
  const visible = shouldShowQualityBadge(props.quality);
  return (
    <span
      data-quality={props.quality}
      title={visible ? view.title : undefined}
      aria-hidden={!visible || undefined}
      className={`inline-flex w-[6.4em] max-w-full shrink-0 items-center whitespace-nowrap rounded py-0.5 ${props.align === "start" ? "justify-start px-0" : `justify-center ${props.compact ? "px-1" : "px-1.5"}`} ${props.compact ? "text-[10px]" : "text-xs"} ${visible ? view.expectedSession ? "text-amber-800 dark:text-amber-400" : TONES[view.tone] : ""}`}
    >
      {visible ? view.label : null}
    </span>
  );
}
