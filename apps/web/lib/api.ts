/**
 * 前端 API 客户端**统一出口**（门面）。
 *
 * 2026-09-15（IMP-005）按业务域拆成 `lib/api/*.ts`：本文件**只做转发**，
 * 不含任何实现 —— 引用方（全仓 78 处 `from "@/lib/api"`）因此零改动。
 *
 * ⚠️ 新增端点请加到**对应域**的分片里，不要往本文件塞实现：
 * 门面一旦重新变厚，这次切片就白做了（守卫见 `lib/api-facade.test.ts`）。
 *
 * `lib/api/internal.ts` **刻意不在此转发**：它是模块内助手，不进公开面。
 */

export * from "./api/client";
export * from "./api/agent";
export * from "./api/alerts";
export * from "./api/events";
export * from "./api/market";
export * from "./api/news";
export * from "./api/paper";
export * from "./api/picks";
export * from "./api/positions";
export * from "./api/review";
export * from "./api/risk";
export * from "./api/themes";
export * from "./api/watchlist";
