/**
 * 助手回复中的实体识别与跳转目标解析（纯函数，可测）。
 *
 * 设计：不做 LLM 侧链接语法（模型记代码不可靠），而是拿后端实体字典
 * （/api/assistant/entity-dict：股票名→代码 + 官方题材名）在渲染层做
 * **最长匹配**。字典外的 6 位数字不认（避免把金额/日期当股票）。
 */

export interface EntityDict {
  stocks: { name: string; code: string }[];
  themes: string[];
}

export interface EntityMatch {
  type: "stock" | "theme";
  /** 命中的原文 */
  text: string;
  /** 个股代码（stock 时存在） */
  code?: string;
  /** 展示名（个股=名称，题材=题材名） */
  name: string;
}

export type EntityMatcher = (text: string) => EntityMatch[];

const IDLE: EntityMatcher = () => [];

function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/** 词典为空/未加载时返回空匹配器——识别是增强层，绝不阻塞渲染。 */
export function createEntityMatcher(dict: EntityDict | null | undefined): EntityMatcher {
  if (!dict) return IDLE;
  const codeToStock = new Map<string, { name: string }>();
  const names: string[] = [];
  for (const s of dict.stocks) {
    if (!s.name || !/^\d{6}$/.test(s.code)) continue;
    if (!codeToStock.has(s.code)) {
      codeToStock.set(s.code, { name: s.name });
      names.push(s.name);
    }
  }
  const themes = (dict.themes ?? []).filter((t) => t.length >= 2);
  if (!names.length && !themes.length) return IDLE;

  // 最长优先：同前缀题材名（"芯片" vs "芯片设备"）命中长的
  const words = [...names, ...themes].sort((a, b) => b.length - a.length);
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
      } else if (names.includes(token)) {
        const code = dict.stocks.find((s) => s.name === token)?.code;
        if (!code) continue;
        out.push({ type: "stock", text: token, code, name: token });
      } else {
        out.push({ type: "theme", text: token, name: token });
      }
    }
    return out;
  };
}
