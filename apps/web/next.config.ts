import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Next 16 默认仅允许 localhost 来源访问 dev HMR / chunk 资源；
  // agent-browser / curl 常用 127.0.0.1，必须显式加白名单，否则 JS 加载被拦截。
  allowedDevOrigins: ["127.0.0.1"],
  // 2026-09-01 系统重构：页面合并后的旧路由 302（dev 下改配置需重启 dev server）。
  // 页面内链接已全部改为新地址，此处仅兜底书签/外部链接；查询参数不保证透传。
  async redirects() {
    return [
      { source: "/themes", destination: "/tape?tab=themes", permanent: false },
      { source: "/limit-up", destination: "/tape?tab=limitup", permanent: false },
      // 评审 D1（2026-09-01）：盘面页板块 tab 移除，板块能力在工作台详情
      { source: "/boards", destination: "/workbench", permanent: false },
      { source: "/longhu", destination: "/tape?tab=longhu", permanent: false },
      { source: "/heatmap", destination: "/market?tab=heatmap", permanent: false },
      { source: "/watchlist", destination: "/workbench", permanent: false },
      { source: "/backtest", destination: "/research?tab=backtest", permanent: false },
      { source: "/alerts", destination: "/research?tab=alerts", permanent: false },
    ];
  },
  // 注：后端反代不要用 rewrites() —— 它在构建期求值并烘进产物，next start 不重读
  // BACKEND_ORIGIN（实测换了后端地址仍然打到构建期默认值）。
  // 改由 app/backend/[...path]/route.ts 在运行时代理。
};

export default nextConfig;
