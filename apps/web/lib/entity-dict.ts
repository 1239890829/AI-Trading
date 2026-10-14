import {API_BASE} from "@/lib/api/client";
import type {EntityDict} from "@/lib/entity-links";

let cached: EntityDict | null | undefined;
let retryAfter = 0;
const RETRY_MS = 60_000;
interface Flight {
  controller: AbortController;
  promise: Promise<EntityDict | null>;
  owners: number;
  settled: boolean;
}
let flight: Flight | null = null;

/** Public entity names only. Preserve the original successful cache and failed cooldown. */
export function loadEntityDict(signal?: AbortSignal): Promise<EntityDict | null> {
  if (signal?.aborted) return Promise.resolve(null);
  if (cached !== undefined) return Promise.resolve(cached);
  if (Date.now() < retryAfter) return Promise.resolve(null);
  let current = flight;
  if (!current) {
    const entry: Flight = {controller: new AbortController(), promise: Promise.resolve(null), owners: 0, settled: false};
    flight = entry;
    current = entry;
    entry.promise = (async () => {
      try {
        const response = await fetch(`${API_BASE}/api/assistant/entity-dict`, {signal: entry.controller.signal});
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        const body = await response.json();
        if (!entry.controller.signal.aborted) cached = (body?.data as EntityDict) ?? null;
        return entry.controller.signal.aborted ? null : cached ?? null;
      } catch {
        if (!entry.controller.signal.aborted) retryAfter = Date.now() + RETRY_MS;
        return null;
      } finally {
        entry.settled = true;
        if (flight === entry) flight = null;
      }
    })();
  }
  const entry = current;
  entry.owners += 1;
  return new Promise(resolve => {
    let finished = false;
    const finish = (value: EntityDict | null) => {
      if (finished) return;
      finished = true;
      signal?.removeEventListener("abort", canceled);
      entry.owners -= 1;
      if (!entry.settled && entry.owners === 0) {
        if (flight === entry) flight = null;
        entry.controller.abort();
      }
      resolve(value);
    };
    const canceled = () => finish(null);
    signal?.addEventListener("abort", canceled, {once: true});
    if (signal?.aborted) canceled();
    else void entry.promise.then(finish);
  });
}

/** Test isolation only; the application never resets a successful dictionary. */
export function __resetEntityDictCache(): void {
  flight?.controller.abort();
  flight = null;
  cached = undefined;
  retryAfter = 0;
}
