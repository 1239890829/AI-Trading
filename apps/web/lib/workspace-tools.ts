/** Presentation groups do not confer permissions or merge business ownership. */
export const RESEARCH_TOOLS = [
  { key: "leaders", label: "龙头样本", description: "从形成过程找共性，再核失败样本", group: "样本与知识", mark: "01" },
  { key: "kb", label: "知识与反证", description: "查依据、适用条件与失效记录", group: "样本与知识", mark: "02" },
  { key: "strategies", label: "方法核验", description: "读取样本、健康判定与核验产物", group: "方法有效性", mark: "03" },
] as const;

export const MAINTENANCE_TOOLS = [
  { key: "tasks", label: "任务与回执", description: "发起受控任务，查看失败与取消结果", group: "运行与处置", mark: "01" },
  { key: "review", label: "改进项处置", description: "核对提案与执行记录后处置", group: "运行与处置", mark: "02" },
  { key: "strategies", label: "策略健康", description: "核对判定口径和原始验证产物", group: "核验与迭代", mark: "03" },
  { key: "evolution", label: "迭代记录", description: "查看进化历史、实验与权限边界", group: "核验与迭代", mark: "04" },
  { key: "params", label: "参数配置", description: "在原鉴权范围内维护生效参数", group: "设置与追踪", mark: "05" },
  { key: "alerts", label: "提醒管理", description: "维护规则，日常提醒仍在通知中心", group: "设置与追踪", mark: "06" },
  { key: "repos", label: "仓库追踪", description: "查看外部候选和同步记录", group: "设置与追踪", mark: "07" },
] as const;

export type WorkspaceTool = { key: string; label: string; description: string; group: string; mark: string };

export const MARKET_LENSES = [
  { key: "overview", label: "概览与环境", group: "市场脉搏", href: "/market", description: "指数、宽度与情绪" },
  { key: "fund", label: "资金流向", group: "市场脉搏", href: "/market?tab=fund", description: "资金与成交结构" },
  { key: "heatmap", label: "市场云图", group: "市场脉搏", href: "/market?tab=heatmap", description: "板块的相对强弱" },
  { key: "events", label: "事件线索", group: "驱动与结构", href: "/market?tab=events", description: "新闻到标的的证据链" },
  { key: "themes", label: "题材梯队", group: "驱动与结构", href: "/tape?tab=themes", description: "共振、前后排与梯队" },
  { key: "limitup", label: "涨停生态", group: "驱动与结构", href: "/tape?tab=limitup", description: "涨停与炸板结构" },
  { key: "limitdown", label: "跌停观察", group: "驱动与结构", href: "/tape?tab=limitdown", description: "弱势与风险分布" },
  { key: "longhu", label: "龙虎榜", group: "资金足迹", href: "/tape?tab=longhu", description: "公开交易席位信息" },
] as const;

/** A lens switch changes route-owned keys only; preserve object/date/return identity. */
export function marketLensUrl(href: string, search: string) {
  const target = new URL(href, "https://example.invalid");
  const params = new URLSearchParams(search);
  params.delete("tab");
  target.searchParams.forEach((value, key) => params.set(key, value));
  return `${target.pathname}${params.size ? `?${params}` : ""}`;
}
