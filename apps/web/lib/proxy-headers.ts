/**
 * 反代发往后端时的**请求头构造**（R22 统一鉴权边界，2026-09-15 从 route.ts 抽出）。
 *
 * 抽出来的唯一理由：这段逻辑的**判据必须是行为式的**。它此前写在 Route Handler 里，
 * 只能靠"在注入语句前 300 字符内找 `req.method !== 'GET'`"这类**字符串相对位置断言**
 * 来守卫——那种断言既脆（挪一行注释就红）又弱（换个写法就漏）。抽成纯函数后，
 * `proxy-headers.test.ts` 直接喂各种 method / 头组合断言**输出头**，判定与实现解耦。
 *
 * ## 为什么现在是「一律注入」而不是「只给写请求注入」
 *
 * 旧口径（B6）只有写接口鉴权，于是"给 GET 也加头"是把凭据撒到所有读请求上，
 * 确实该防。R22 之后口径变了：后端是**默认拒绝**（除 `/api/health` 外全部路由
 * 都要凭据，含 `/assistant/daily-summary`、`/system/llm-probe` 这类花钱 GET）。
 * 此时"只给非 GET 注入"反而成了缺口——所有读请求都会 401。
 *
 * 一律注入也顺带消掉了**跨语言的第二份清单**：若按路径白名单注入，前端就得
 * 维护一份"哪些路径是敏感读"的清单，与后端的守卫清单必须永远一致——两份清单
 * 必然漂移。现在前端零清单：后端决定谁要凭据，前端只管把凭据带上。
 *
 * ## 保密约束（不可回退）
 *
 * token **只从服务端 `process.env` 运行时读取**，绝不下发浏览器：
 * `NEXT_PUBLIC_*` 会被 Next **构建期内联**成客户端的字面量，任何访客查看源码即可取得。
 * 该禁令由 `lib/env-secrecy.test.ts` 机械守卫（本条注释必须在那个文件的扫描面里
 * 以"提及而非使用"的形式存在，故写法上刻意不出现对它的真实取值）。
 */

/** 客户端送来的凭据头一律丢弃，只认服务端环境变量（防伪造 / 防误配）。 */
const CLIENT_TOKEN_HEADER = "x-api-token";

/**
 * 依据入站请求头构造发往后端的请求头。
 *
 * @param inbound 浏览器送来的请求头（本函数会复制，不改动入参）
 * @param token   服务端持有的共享凭据；未配置时传 `undefined` / 空串
 *
 * @remarks
 * - 未配置 token ⇒ 不带头。这与后端 `local` 姿态对称（本地开发零影响）；
 *   后端配了而这里没配 ⇒ 全部 401，属**吵闹失效**（不是静默放行），可接受。
 * - `host` / `content-length` 必须删掉：前者会让后端按 Next 自己的 Host 处理
 *   （CORS 校验出错），后者由 fetch 依据实际 body 重算，透传旧值会不一致。
 */
export function buildUpstreamHeaders(inbound: Headers, token: string | undefined): Headers {
  const headers = new Headers(inbound);
  headers.delete("host");
  headers.delete("content-length");
  headers.delete(CLIENT_TOKEN_HEADER);
  if (token) headers.set(CLIENT_TOKEN_HEADER, token);
  return headers;
}
