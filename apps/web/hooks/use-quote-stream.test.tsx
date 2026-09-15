import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useQuoteStream } from "@/hooks/use-quote-stream";

/**
 * R22（2026-09-15）：行情 WS 连接必须携带**子协议凭据**，且不破坏既有的
 * 重连 / 降级链路。
 *
 * 为什么单开一条而不是并进 `lib/ws-credential.test.ts`：那边守的是"凭据怎么取
 * （缓存 / 失败重试）"，这边守的是"取到之后**怎么用**"——`connect()` 为取凭据
 * 变成了 async，多了一个 await 点，而 await 期间组件可能已卸载。这个新引入的
 * 窗口只能靠钩子级测试钉住（`closed` 判定若漏掉，会创建一个无人回收的 socket）。
 */

const cred = vi.hoisted(() => ({ value: [] as string[], calls: 0 }));
vi.mock("@/lib/ws-credential", () => ({
  wsSubprotocols: async () => {
    cred.calls += 1;
    return cred.value;
  },
}));

vi.mock("@/lib/api", () => ({
  wsBase: () => "ws://test.local/backend",
  getQuotes: async () => [],
}));

class FakeWS {
  static instances: FakeWS[] = [];
  static OPEN = 1;
  readyState = 0;
  onopen: (() => void) | null = null;
  onmessage: ((ev: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;
  readonly url: string;
  readonly protocols: string[] | undefined;

  constructor(url: string, protocols?: string[]) {
    this.url = url;
    this.protocols = protocols;
    FakeWS.instances.push(this);
  }

  close() {
    this.readyState = 3;
  }

  send() {
    /* noop */
  }
}

/** 让 `connect()` 里那个 `await wsSubprotocols()` 结算。 */
async function settle() {
  await act(async () => {
    await Promise.resolve();
  });
}

beforeEach(() => {
  FakeWS.instances = [];
  cred.value = [];
  cred.calls = 0;
  vi.stubGlobal("WebSocket", FakeWS);
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("行情 WS 的子协议凭据（R22）", () => {
  it("配置了凭据 ⇒ 建连时带上子协议", async () => {
    cred.value = ["ashare-token.YWJj"];
    const { unmount } = renderHook(() => useQuoteStream(["600519"]));
    await settle();
    expect(FakeWS.instances).toHaveLength(1);
    expect(FakeWS.instances[0].protocols).toEqual(["ashare-token.YWJj"]);
    expect(FakeWS.instances[0].url).toContain("/ws/quotes?symbols=600519");
    unmount();
  });

  it("未配置凭据 ⇒ 建连不带第二个参数（本地 develop 零摩擦，不引入空协议数组）", async () => {
    cred.value = [];
    const { unmount } = renderHook(() => useQuoteStream(["600519"]));
    await settle();
    expect(FakeWS.instances).toHaveLength(1);
    // 关键：`new WebSocket(url, [])` 与 `new WebSocket(url)` 在浏览器里
    // **不等价**——传空数组会让服务端收到空的 Sec-WebSocket-Protocol。
    expect(FakeWS.instances[0].protocols).toBeUndefined();
    unmount();
  });

  it("断线重连后仍带凭据（凭据是连接期常量，不因重连丢失）", async () => {
    cred.value = ["ashare-token.YWJj"];
    const { unmount } = renderHook(() => useQuoteStream(["600519"]));
    await settle();
    expect(FakeWS.instances).toHaveLength(1);

    // 模拟服务端断开 → 重连（retry=1 ⇒ 退避 2s）
    await act(async () => {
      FakeWS.instances[0].onclose?.();
      await vi.advanceTimersByTimeAsync(2100);
    });
    await settle();
    expect(FakeWS.instances.length).toBeGreaterThanOrEqual(2);
    expect(FakeWS.instances.at(-1)?.protocols).toEqual(["ashare-token.YWJj"]);
    unmount();
  });

  it("取凭据期间卸载 ⇒ 不得再建 socket（await 窗口的竞态）", async () => {
    cred.value = ["ashare-token.YWJj"];
    const { unmount } = renderHook(() => useQuoteStream(["600519"]));
    // 不 settle，直接卸载：此时 connect() 正挂在 await 上
    unmount();
    await settle();
    expect(FakeWS.instances).toHaveLength(0);
  });
});
