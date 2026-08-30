import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Next 16 默认仅允许 localhost 来源访问 dev HMR / chunk 资源；
  // agent-browser / curl 常用 127.0.0.1，必须显式加白名单，否则 JS 加载被拦截。
  allowedDevOrigins: ["127.0.0.1"],
  // 注：后端反代不要用 rewrites() —— 它在构建期求值并烘进产物，next start 不重读
  // BACKEND_ORIGIN（实测换了后端地址仍然打到构建期默认值）。
  // 改由 app/backend/[...path]/route.ts 在运行时代理。
};

export default nextConfig;
