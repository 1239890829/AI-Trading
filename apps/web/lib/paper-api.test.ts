import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { placePaperOrder } from "./api/paper";
import { sendJson } from "./api/internal";
import { ApiError } from "./api/client";

vi.mock("./api/internal", () => ({ sendJson: vi.fn(), getJson: vi.fn(), getJsonArray: vi.fn() }));
beforeEach(() => { sessionStorage.clear(); vi.clearAllMocks(); });
afterEach(() => vi.restoreAllMocks());

it("lost response retry keeps the original identity and deadline", async () => {
  vi.mocked(sendJson).mockRejectedValueOnce(new ApiError(0, "timeout", "timeout"));
  await expect(placePaperOrder("600127", "buy", 10, 100)).rejects.toThrow("timeout");
  const first = vi.mocked(sendJson).mock.calls[0][2];
  vi.mocked(sendJson).mockResolvedValue({ data: { id: 1, status: "filled", replayed: true }, meta: { provider: "fixture", is_realtime: false, is_stale: false, last_success_refresh: null, generated_at: "2026-09-29T10:00:00+08:00" } });
  const result = await placePaperOrder("600127", "buy", 10, 100);
  expect(vi.mocked(sendJson).mock.calls[1][2]).toEqual(first);
  expect(result.replayed).toBe(true);
  expect(sessionStorage.length).toBe(0);
});

it("concurrent clicks share one request; an expired rejection requires a new confirmation", async () => {
  let reject!: (e: Error) => void;
  vi.mocked(sendJson).mockReturnValueOnce(new Promise((_, r) => { reject = r; }));
  const a = placePaperOrder("600127", "buy", 10, 100);
  const b = placePaperOrder("600127", "buy", 10, 100);
  expect(a).toBe(b);
  expect(sendJson).toHaveBeenCalledTimes(1);
  const old = vi.mocked(sendJson).mock.calls[0][2] as { request_id: string };
  reject(new ApiError(422, "expired", "http_422"));
  await expect(a).rejects.toThrow("expired");
  vi.mocked(sendJson).mockResolvedValue({ data: { id: 2, status: "filled" }, meta: { provider: "fixture", is_realtime: false, is_stale: false, last_success_refresh: null, generated_at: "2026-09-29T10:00:00+08:00" } });
  await placePaperOrder("600127", "buy", 10, 100);
  const next = vi.mocked(sendJson).mock.calls[1][2] as { request_id: string };
  expect(next.request_id).not.toBe(old.request_id);
});

it("storage failure prevents an untraceable submission", async () => {
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("quota"); });
  await expect(placePaperOrder("600127", "buy", 10, 100)).rejects.toThrow("quota");
  expect(sendJson).not.toHaveBeenCalled();
});
