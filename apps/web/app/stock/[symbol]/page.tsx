import { redirect } from "next/navigation";
import { stockRedirectTarget } from "@/lib/routing";

/**
 * 个股语义路由（/stock/600103）→ 工作台详情。
 *
 * 2026-08-31 修复跨页面联动 bug：本页此前只读 searchParams（查询参数），
 * 而题材卡 / 涨停梯队 / 龙虎榜 / 板块 / 自选管理全部以路径参数链接（/stock/002855），
 * 导致 symbol 永远为空 → 一律重定向到不带参数的 /workbench →
 * 详情回退到默认标的。表现即「从题材榜点个股，看到的还是之前的自选股」。
 * 现在 params（路径段）优先，查询参数 /stock?symbol=600103 作为兼容形态保留。
 */
export default async function StockPage({
  params,
  searchParams,
}: {
  params: Promise<{ symbol?: string }>;
  searchParams: Promise<{ symbol?: string }>;
}) {
  const [{ symbol: pathSymbol }, sp] = await Promise.all([params, searchParams]);
  const target = stockRedirectTarget(pathSymbol, sp?.symbol);
  redirect(target ?? "/workbench");
}
