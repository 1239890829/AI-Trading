import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { resetWsCredentialCache, wsSubprotocols } from "@/lib/ws-credential";

/**
 * WS 子协议凭据的取用语义（R22，2026-09-15）。
 *
 * 这里守的主要是**缓存策略**——它是最容易写错、且写错后最难排查的一块：
 * - 成功缓存（含"未配置 ⇒ null"）：值是连接期常量，不必每次重连都取；
 * - **失败不缓存**：把一次网络抖动缓存住，等于让整个会话永久降级为 REST 轮询，
 *   而界面上只会显示一个中性的"WS 断线 · REST 轮询"，排查时完全无从下手。
 */

const fetchMock = vi.fn();

beforeEach(() => {
  resetWsCredentialCache();
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  vi.unstubAllGlobals();
  resetWsCredentialCache();
});

function jsonOnce(body: unknown, ok = true) {
  fetchMock.mockResolvedValueOnce({
    ok,
    json: async () => body,
  });
}

describe("WS 子协议凭据", () => {
  it("服务端下发凭据时返回单元素子协议数组", async () => {
    jsonOnce({ subprotocol: "ashare-token.YWJj" });
    expect(await wsSubprotocols()).toEqual(["ashare-token.YWJj"]);
  });

  it("未配置凭据时返回空数组（本地默认 ⇒ 调用方不传第二个参数）", async () => {
    jsonOnce({ subprotocol: null });
    expect(await wsSubprotocols()).toEqual([]);
  });

  it("请求带 no-store（凭据不得被任何中间层缓存）", async () => {
    jsonOnce({ subprotocol: null });
    await wsSubprotocols();
    expect(fetchMock).toHaveBeenCalledWith("/api/ws-credential", { cache: "no-store" });
  });

  it("成功结果被缓存：多次调用只取一次", async () => {
    jsonOnce({ subprotocol: "ashare-token.YWJj" });
    await wsSubprotocols();
    await wsSubprotocols();
    await wsSubprotocols();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("'未配置'也算成功结果 —— 同样缓存（否则每次重连都白跑一次同源请求）", async () => {
    jsonOnce({ subprotocol: null });
    await wsSubprotocols();
    await wsSubprotocols();
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("**网络失败不缓存**：下次调用必须重试", async () => {
    fetchMock.mockRejectedValueOnce(new Error("offline"));
    expect(await wsSubprotocols()).toEqual([]);
    jsonOnce({ subprotocol: "ashare-token.YWJj" });
    // 若把失败也缓存住，这里会拿到 []（= 会话永久降级为轮询）而不是重试后的凭据
    expect(await wsSubprotocols()).toEqual(["ashare-token.YWJj"]);
  });

  it("HTTP 非 2xx 同样不缓存、不抛（降级为不带子协议，由后端决定是否放行）", async () => {
    jsonOnce({ detail: "boom" }, false);
    expect(await wsSubprotocols()).toEqual([]);
    jsonOnce({ subprotocol: "ashare-token.YWJj" });
    expect(await wsSubprotocols()).toEqual(["ashare-token.YWJj"]);
  });

  it("响应缺字段时按'未配置'处理（不把 undefined 塞进子协议数组）", async () => {
    // 子协议数组里的 undefined 会让 `new WebSocket(url, [undefined])` 抛异常，
    // 而那是**前端**的失败，后端只看到"连接没建立"。
    jsonOnce({});
    expect(await wsSubprotocols()).toEqual([]);
  });
});
