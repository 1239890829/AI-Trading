import { describe, expect, it } from "vitest";

import { buildUpstreamHeaders } from "@/lib/proxy-headers";

/**
 * 反代请求头构造的行为式判据（R22 统一鉴权边界，2026-09-15）。
 *
 * 取代了原先写在 `app/backend/[...path]/route.ts` 上的**字符串相对位置断言**
 * （"注入语句前 300 字符内必须有 `req.method !== 'GET'`"）。那种断言有两个毛病：
 * 挪一行注释就红（脆），把判断挪进函数就漏（弱）。这里直接断言**输出头**，
 * 与实现形态解耦。
 */

const ALL_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"] as const;

describe("反代请求头构造", () => {
  it("配了凭据时**所有方法**都注入（含 GET/HEAD）", () => {
    // 这是 R22 的核心口径：后端默认拒绝，除 /api/health 外每条路由都要凭据。
    // 只给非 GET 注入 ⇒ 所有读请求 401（incl. /real/positions、/system/llm-probe）。
    for (const method of ALL_METHODS) {
      const out = buildUpstreamHeaders(new Headers(), "s3cret");
      expect(out.get("x-api-token"), `${method} 未注入凭据`).toBe("s3cret");
    }
  });

  it("未配凭据时不注入任何头（与后端 local 姿态对称，本地零影响）", () => {
    for (const absent of [undefined, ""]) {
      const out = buildUpstreamHeaders(new Headers(), absent);
      expect(out.has("x-api-token")).toBe(false);
    }
  });

  it("客户端伪造的凭据头一律丢弃（只认服务端环境变量）", () => {
    const inbound = new Headers({ "x-api-token": "forged" });
    // 配了凭据 ⇒ 用服务端的值覆盖伪造值
    expect(buildUpstreamHeaders(inbound, "real").get("x-api-token")).toBe("real");
    // 没配凭据 ⇒ 伪造值也必须消失（否则访客可自行开启"受保护"假象 / 撞对真值）
    expect(buildUpstreamHeaders(inbound, undefined).has("x-api-token")).toBe(false);
  });

  it("host 与 content-length 必须删掉", () => {
    // host：不删则后端按 Next 自己的 Host 处理，CORS 校验会出错
    // content-length：由 fetch 依据实际 body 重算，透传旧值会不一致
    const out = buildUpstreamHeaders(
      new Headers({ host: "next.local", "content-length": "999", "x-keep": "1" }),
      undefined,
    );
    expect(out.has("host")).toBe(false);
    expect(out.has("content-length")).toBe(false);
    expect(out.get("x-keep")).toBe("1"); // 其余头正常透传
  });

  it("不改动入参（纯函数，便于并发复用同一个 Headers）", () => {
    const inbound = new Headers({ host: "next.local", "x-api-token": "forged" });
    buildUpstreamHeaders(inbound, "real");
    expect(inbound.get("host")).toBe("next.local");
    expect(inbound.get("x-api-token")).toBe("forged");
  });
});
