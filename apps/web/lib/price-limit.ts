/**
 * 个股涨跌幅限制（%），按代码段判定板块（2026-09-04 用户需求，分时图纵轴用）：
 * 沪深主板 60/00 → 10；创业板 30（含 **302**）、科创板 68 → 20；
 * 北交所 43 / 83 / 87 / 88 / 92 → 30。
 *
 * **ST 不再降档（2026-07-06 起）**：沪深交易所 2026-04-24 修订《交易规则》、
 * 7/6 生效——主板风险警示股（ST/*ST）涨跌幅由 5% 放宽至 10%，与主板普通股
 * 并轨；创业板/科创板 ST 维持 20%、北交所维持 30%。故本函数**只按代码段判定**，
 * `name` 参数保留以兼容既有调用签名（详情页仍传 quote.name），不再参与计算。
 * 口径单点在后端 `app/market/price_rules.limit_pct`，本文件与其对齐，
 * 跨端由 backend/tests/golden/price_limit_golden.json 双端同批校验。
 *
 * 代码段依据（2026-09-11 双端补齐，golden 补 required 段）：
 * - 302 段属创业板 20%：**实测** 302132.SZ 在 2023-02 / 2025-05 多次收 +20.01%
 *   触板（后端此前只列 300/301 ⇒ 判 10%，已漂移，本轮后端同步修正）。
 * - 88 段属北交所普通股票：依《北京证券交易所全国中小企业股份转让系统证券代码、
 *   证券简称编制指引》第七条（普通股票首两位为 83、87、88）→ 30%。
 *
 * ⚠️ **边界（勿踩）**：88 段是**歧义段**——同花顺板块指数（本项目题材目录
 * `theme.code`，实测 885xxx/886xxx）也落在 88 段，而**指数无涨跌停概念**。
 * 本函数只服务**个股** symbol：下方指数前缀守卫（sh880 / 裸 880/899/399）
 * 必须保留在段判定之前；调用方（stock-detail）只传个股。指数要走三态 null，
 * 不做「兜底一个数」假装有值。
 *
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
  if (code.startsWith("30") || code.startsWith("68")) return 20;
  if (code.startsWith("60") || code.startsWith("00")) return 10;
  if (/^(43|83|87|88|92)/.test(code)) return 30;
  return null;
}
// 注：`name` 为保留参数（调用方签名兼容 + 未来规则回退时的显式落点）。
// 2026-07-06 并轨前主板/双创 ST 曾收窄至 5%，该分支已随新规删除。
