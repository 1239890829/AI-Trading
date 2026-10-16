/** One ticket per connection. Concurrent sockets must never share a ticket. */
import {deviceSessionEpoch} from "./api/internal";

export async function wsSubprotocols(): Promise<string[]> {
  const session = deviceSessionEpoch();
  try {
    const response = await fetch("/api/ws-credential", {cache: "no-store"});
    if (session !== deviceSessionEpoch()) return [];
    if (response.status === 401 && typeof window !== "undefined") window.dispatchEvent(new Event("ashare-access-expired"));
    if (!response.ok) return [];
    const body = await response.json() as {subprotocol?: unknown};
    return typeof body.subprotocol === "string" && /^ashare-ticket\.[A-Za-z0-9_-]+$/.test(body.subprotocol) ? [body.subprotocol] : [];
  } catch { return []; }
}

/** Compatibility for existing test harnesses; there is no credential cache. */
export function resetWsCredentialCache(): void {}
