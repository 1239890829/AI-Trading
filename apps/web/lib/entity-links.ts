/**
 * 助手回复中的实体识别与跳转目标解析（纯函数，可测）。
 *
 * 设计：不做 LLM 侧链接语法（模型记代码不可靠），而是拿后端实体字典
 * （/api/assistant/entity-dict：股票名→代码 + 官方题材名）在渲染层做
 * **最长匹配**。字典外的 6 位数字不认（避免把金额/日期当股票）。
 *
 * 2026-09-06 扩面（docs/assistant-optimization-plan.md §1）：除个股/题材外，
 * 还识别**功能入口别名**（"涨停池""龙虎榜""复盘报告"…），命中即渲染成站内跳转
 * 链接。别名表与 URL 构造都在 lib/nav-targets.ts，本文件只做识别。
 *
 * 2026-09-11（P2-28②）再扩一层：**「个股 + 页签」组合**。此前个股一律落
 * 工作台首页，助手说"到分时/资金页签看"时点不到位——页签构造器早已就绪
 * （`stock_minute` 等），缺的是识别。现在扫到个股后会看它**紧后面**是否跟着
 * 页签词（只允许夹空白与一个「的」），命中即产出带参深链并通过守卫。
 */
import { NAV_ALIASES, STOCK_TAB_ALIASES, buildNav, navAliasWords, stockTabWords } from "@/lib/nav-targets";
import type { NavKey } from "@/lib/nav-targets";

export interface EntityDict {
  stocks: { name: string; code: string }[];
  themes: string[];
}

export interface EntityMatch {
  type: "stock" | "theme" | "nav";
  /** 命中的原文 */
  text: string;
  /** 个股代码（stock 时存在） */
  code?: string;
  /** 展示名（个股=名称，题材=题材名，功能入口=功能名） */
  name: string;
  /**
   * 落点 key：nav 必为功能入口 key；stock 命中「个股 + 页签」组合时
   * 为 `stock_minute` 等页签 key（未命中页签则不存在，落工作台首页）。
   */
  key?: string;
  /** 落点 URL（nav 与「个股 + 页签」时存在；已经过白名单守卫，可直接 router.push） */
  url?: string;
}

export type EntityMatcher = (text: string) => EntityMatch[];

const IDLE: EntityMatcher = () => [];

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * 个股与页签词之间允许的间隔：**只允许空白与一个「的」**。
 *
 * 刻意不放开成"任意若干字"——"贵州茅台今日分时走弱"里"今日"已经把它变成
 * 两个独立片段，硬连会把不相干的分时描述挂到这只票上。窄口径宁可少连，
 * 也不要在正文里乱插链接（同 nav 别名只收具体词的既有纪律）。
 */
const TAB_GAP = /^\s*的?\s*/;

/**
 * 从 `start` 起找出紧跟的页签词（最长匹配优先）。返回 key 与"吃到哪儿"。
 * 页签词**不在**主扫描的候选词里，只经本函数消费——否则"今天分时怎么样"
 * 这种无个股的句子会被误标成链接。
 */
function tabAfter(
  text: string,
  start: number,
  tabWords: readonly string[],
): { key: NavKey; end: number } | null {
  const gap = TAB_GAP.exec(text.slice(start));
  const at = start + (gap ? gap[0].length : 0);
  for (const w of tabWords) {
    if (text.startsWith(w, at)) return { key: STOCK_TAB_ALIASES[w], end: at + w.length };
  }
  return null;
}

/**
 * 词典为空/未加载时返回空匹配器——识别是增强层，绝不阻塞渲染。
 *
 * 三类命中混在同一个正则里（个股名 > 题材名 > 功能别名），全部按**最长优先**
 * 参与同一轮扫描，避免"先扫个股再扫功能"导致的功能名被个股名切断。
 */
export function createEntityMatcher(dict: EntityDict | null | undefined): EntityMatcher {
  const navWords = navAliasWords();
  const tabWords = stockTabWords();
  const stocks = dict?.stocks ?? [];
  const themes = (dict?.themes ?? []).filter((t) => t.length >= 2);

  const codeToStock = new Map<string, { name: string }>();
  const names: string[] = [];
  for (const s of stocks) {
    if (!s.name || !/^\d{6}$/.test(s.code)) continue;
    if (!codeToStock.has(s.code)) {
      codeToStock.set(s.code, { name: s.name });
      names.push(s.name);
    }
  }
  const nameToCode = new Map<string, string>();
  for (const s of stocks) if (s.name && /^\d{6}$/.test(s.code)) nameToCode.set(s.name, s.code);
  const themeSet = new Set(themes);
  if (!names.length && !themeSet.size && !navWords.length) return IDLE;

  // 最长优先：同前缀题材名（"芯片" vs "芯片设备"）命中长的
  const words = [...names, ...themeSet, ...navWords].sort((a, b) => b.length - a.length);
  const alternation = words.map(escapeRe).join("|");
  // 前后都不贴字母/数字：防 "OpenAI" 里命中 "AI"、日期 "20260904" 里命中代码
  const re = new RegExp(`(?<![0-9A-Za-z])(?:\\d{6}|${alternation})(?![0-9A-Za-z])`, "g");

  return function match(text: string): EntityMatch[] {
    if (!text) return [];
    const out: EntityMatch[] = [];
    re.lastIndex = 0;
    let m: RegExpExecArray | null;
    while ((m = re.exec(text)) !== null) {
      const token = m[0];
      // 先解析「个股」候选（名称或字典内代码），两条来源共用同一段组合识别逻辑
      let code: string | null = null;
      let name = token;
      if (/^\d{6}$/.test(token)) {
        const stock = codeToStock.get(token);
        // 字典外的 6 位数字（金额/手数等）不当个股
        if (!stock) continue;
        code = token;
        name = stock.name;
      } else if (nameToCode.has(token)) {
        code = nameToCode.get(token) ?? null;
      }

      if (code) {
        // 「个股 + 页签」：紧跟页签词则直达该页签，并把页签词一并吃掉，
        // 否则它会在下一轮被当成独立功能别名（"资金流向"→市场资金）。
        const tab = tabAfter(text, m.index + token.length, tabWords);
        const url = tab ? buildNav(tab.key, code) : null;
        if (tab && url) {
          out.push({ type: "stock", text: token, code, name, key: tab.key, url });
          re.lastIndex = tab.end;
        } else {
          out.push({ type: "stock", text: token, code, name });
        }
      } else if (themeSet.has(token)) {
        out.push({ type: "theme", text: token, name: token });
      } else {
        // 功能别名：URL 必须过白名单守卫，未过即降级为纯文本（绝不渲染成外链）
        const key = NAV_ALIASES[token];
        const url = key ? buildNav(key) : null;
        if (!key || !url) continue;
        out.push({ type: "nav", text: token, name: token, key, url });
      }
    }
    return out;
  };
}
