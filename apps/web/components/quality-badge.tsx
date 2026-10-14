import { shouldShowQualityBadge } from "@/lib/format";
import { quoteQualityView, type QuoteQualityInput } from "@/lib/quote-quality-view";

const TONES = {
  quiet: "text-zinc-600 dark:text-zinc-400",
  warning: "bg-amber-500/10 text-orange-800 dark:text-orange-400",
  danger: "bg-red-500/10 text-red-700 dark:text-red-400",
};

/** Keep one stable slot while reporting actual source quality without a debounce. */
export function QualityBadge(props: QuoteQualityInput & {compact?: boolean; align?: "start" | "center"}) {
  if (!props.quality) return null;
  const view = quoteQualityView(props);
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
