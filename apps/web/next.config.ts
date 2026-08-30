import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Next 16 默认仅允许 localhost 来源访问 dev HMR / chunk 资源；
  // agent-browser / curl 常用 127.0.0.1，必须显式加白名单，否则 JS 加载被拦截。
  allowedDevOrigins: ["127.0.0.1"],
  async rewrites() {
    const backend = process.env.BACKEND_ORIGIN ?? "http://127.0.0.1:8000";
    return [{ source: "/backend/:path*", destination: `${backend}/:path*` }];
  },
};

export default nextConfig;
