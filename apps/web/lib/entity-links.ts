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
 */
import { NAV_ALIASES, buildNav, navAliasWords } from "@/lib/nav-targets";

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
  /** 功能入口 key（nav 时存在） */
  key?: string;
  /** 功能入口 URL（nav 时存在；已经过白名单守卫，可直接 router.push） */
  url?: string;
}

export type EntityMatcher = (text: string) => EntityMatch[];

const IDLE: EntityMatcher = () => [];

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * 词典为空/未加载时返回空匹配器——识别是增强层，绝不阻塞渲染。
 *
 * 三类命中混在同一个正则里（个股名 > 题材名 > 功能别名），全部按**最长优先**
 * 参与同一轮扫描，避免"先扫个股再扫功能"导致的功能名被个股名切断。
 */
export function createEntityMatcher(dict: EntityDict | null | undefined): EntityMatcher {
  const navWords = navAliasWords();
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
      if (/^\d{6}$/.test(token)) {
        const stock = codeToStock.get(token);
        // 字典外的 6 位数字（金额/手数等）不当个股
        if (!stock) continue;
        out.push({ type: "stock", text: token, code: token, name: stock.name });
      } else if (nameToCode.has(token)) {
        out.push({ type: "stock", text: token, code: nameToCode.get(token)!, name: token });
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
