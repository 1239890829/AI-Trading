export interface PlotPoint {id: string; label: string; value: number | null}

/** Display-only extrema reduction for complete curves. Gapped series remain
 * intact: a reduced line must never bridge an unavailable observation.
 * Original ordinal positions, denominators and records are preserved by callers.
 */
export function plotSample(points: PlotPoint[], maximum = 240): PlotPoint[] {
  if (points.length <= maximum || points.some(p => p.value == null || !Number.isFinite(p.value))) return points;
  const buckets = Math.max(1, Math.floor((maximum - 2) / 2));
  const width = Math.ceil((points.length - 2) / buckets);
  const chosen = new Set([0, points.length - 1]);
  for (let start = 1; start < points.length - 1; start += width) {
    let min = start, max = start;
    for (let i = start + 1; i < Math.min(start + width, points.length - 1); i++) {
      if (points[i].value! < points[min].value!) min = i;
      if (points[i].value! > points[max].value!) max = i;
    }
    chosen.add(min); chosen.add(max);
  }
  return [...chosen].sort((a, b) => a-b).map(i => points[i]);
}
