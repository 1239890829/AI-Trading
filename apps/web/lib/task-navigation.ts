/** Product navigation is a projection of existing routes, never an account or permission. */
export const TASK_LINKS = [
  { id: "market", href: "/market", label: "市场全景", paths: ["/market", "/tape"] },
  { id: "opportunity", href: "/hunting", label: "机会发现", paths: ["/hunting"] },
  { id: "workspace", href: "/workbench?mode=watch", label: "工作台", paths: ["/workbench"] },
  { id: "research", href: "/agent?area=research&tab=review", label: "复盘研究", paths: ["/agent"] },
] as const;

const MAINTENANCE_TABS = new Set(["operations", "evolution", "tasks", "alerts", "params", "repos"]);

export function agentArea(area: string | null, tab: string | null) {
  if (area === "maintenance") return "maintenance";
  if (MAINTENANCE_TABS.has(tab ?? "")) return "maintenance";
  return "research";
}

export function activeTask(path: string, params: URLSearchParams): string {
  if (path === "/workbench") return "workspace";
  if (path === "/agent") return agentArea(params.get("area"), params.get("tab")) === "maintenance" ? "maintenance" : "research";
  return TASK_LINKS.find((item) => item.paths.some((p) => p === path))?.id ?? "";
}

/** Carry actual object/filter identity once. URLSearchParams owns encoding. */
export function patchWorkspaceUrl(path: string, search: string, patch: Record<string, string | null>) {
  const params = new URLSearchParams(search);
  for (const [key, value] of Object.entries(patch)) {
    if (value === null) params.delete(key);
    else params.set(key, value);
  }
  return `${path}${params.size ? `?${params.toString()}` : ""}`;
}
