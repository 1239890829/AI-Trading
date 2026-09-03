/**
 * 个股涨跌幅限制（%），按代码段判定板块（2026-09-04 用户需求，分时图纵轴用）：
 * 沪深主板 60/00 → 10；创业板 30、科创板 68 → 20；北交所 43/83/87/88/92 → 30；
 * ST（名称含 ST，仅主板/创业/科创）→ 5。
 * 指数（sh000/sh880、sz399、bj899 等指数段）无涨跌停概念 → null，
 * 调用方回退「当日波幅对称区间」。无法识别的代码也返回 null——
 * 判定类字段三态：判不出不猜，绝不给默认 10%。
 */
export function priceLimitPct(symbol: string, name?: string | null): number | null {
  if (!symbol) return null;
  const s = symbol.trim().toLowerCase();
  // 指数段：详情页 symbol 规范带前缀（sh000001 上证指数 vs 裸 000001 平安银行），
  // 带前缀判定优先；裸 399/880/899 段兜底（个股不含这些段）。
  if (/^(sh000|sh880|sh950|sz399|bj899)/.test(s)) return null;
  const code = s.replace(/^(sh|sz|bj)/, "");
  if (/^(399|880|899)/.test(code)) return null;
  if (!/^\d{6}$/.test(code)) return null;
  let base: number;
  if (code.startsWith("30") || code.startsWith("68")) base = 20;
  else if (code.startsWith("60") || code.startsWith("00")) base = 10;
  else if (/^(43|83|87|88|92)/.test(code)) base = 30;
  else return null;
  // ST/*ST：仅主板/创业板/科创板收窄为 ±5%（北交所无 ST 5% 规则，保持 30%）
  if (base !== 30 && name != null && name.toUpperCase().includes("ST")) return 5;
  return base;
}
