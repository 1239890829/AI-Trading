/**
 * 取 WebSocket 子协议凭据（R22，2026-09-15）。
 *
 * 后端 `/ws/quotes` 在启用鉴权后要求子协议携带凭据，而浏览器无法给 WebSocket
 * 设置自定义请求头 ⇒ 凭据只能由**同源**的 `/api/ws-credential` 在运行时下发
 * （服务端读 `ASHARE_API_TOKEN`，不进构建产物；理由与残留风险见那个 route 文件）。
 *
 * 抽成独立模块的两个理由：
 * 1. **可测**：缓存与失败重试是这里最容易写错的部分（失败若被缓存，一次网络抖动
 *    就会让整个会话失去 WS，且表现为"永远走轮询"而无人知道为什么）。
 * 2. **可复位**：测试之间必须能清缓存，否则用例互相污染。
 *
 * ## 缓存语义（刻意如此）
 *
 * - 成功（含 `subprotocol: null`）⇒ 缓存。值是**连接期常量**，不必每次重连都取。
 * - 失败 ⇒ **不缓存**，且清掉 promise 让下次重连重试。把失败也缓存住等于把
 *   一次瞬时抖动升级成"本会话永久降级为轮询"，而界面上只会显示一个中性的
 *   "REST 轮询"，排查时无从下手。
 */

const ENDPOINT = "/api/ws-credential";

let cached: Promise<string[]> | undefined;

async function fetchSubprotocols(): Promise<string[]> {
  const resp = await fetch(ENDPOINT, { cache: "no-store" });
  if (!resp.ok) {
    // ⚠️ 非 2xx **必须走"失败"路径（抛）而不是"返回空数组"**：返回空数组是一个
    // **已兑现**的 promise，外层 catch 不会触发 ⇒ 缓存被留下 ⇒ 整个会话从此
    // 永远不带子协议（表现为"一直走 REST 轮询"，且界面只显示中性的降级文案，
    // 排查时完全看不出是这里）。首版就是这么写的，被
    // `ws-credential.test.ts::HTTP 非 2xx 同样不缓存` 抓到。
    throw new Error(`ws-credential 端点返回 ${resp.status}`);
  }
  const body = (await resp.json()) as { subprotocol?: string | null };
  // 缺字段按"未配置"处理：`undefined` 塞进子协议数组会让
  // `new WebSocket(url, [undefined])` **在前端抛异常**，而后端只看到"连接没建立"。
  return body.subprotocol ? [body.subprotocol] : [];
}

/**
 * 返回应当传给 `new WebSocket(url, protocols)` 的子协议数组。
 *
 * 未配置凭据（本地默认）时返回**空数组** ⇒ 调用方不带第二个参数 ⇒ 与加固前行为一致。
 * `local` 姿态下这条路径不会有任何额外的网络往返可见效果（一次同源 GET）。
 */
export function wsSubprotocols(): Promise<string[]> {
  if (!cached) {
    cached = fetchSubprotocols().catch(() => {
      cached = undefined; // 失败不缓存：下次重连再试
      return [] as string[];
    });
  }
  return cached;
}

/** 仅供测试：清掉模块级缓存，避免用例之间互相污染。 */
export function resetWsCredentialCache(): void {
  cached = undefined;
}
