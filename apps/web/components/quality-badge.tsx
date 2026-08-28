import { qualityLabel } from "@/lib/format";
import type { Quality } from "@/types/market";

const STYLES: Record<Quality, string> = {
  high: "text-zinc-400",
  medium: "text-sky-400",
  low: "text-amber-400",
  stale: "text-orange-400",
  invalid: "text-red-400",
};

export function QualityBadge({ quality, reasons }: { quality: Quality; reasons?: string[] }) {
  const flag = quality !== "high";
  return (
    <span
      title={reasons && reasons.length > 0 ? reasons.join("; ") : undefined}
      className={`rounded px-1.5 py-0.5 text-xs ${flag ? "bg-amber-500/10" : ""} ${STYLES[quality]}`}
    >
      {qualityLabel(quality)}
    </span>
  );
}
