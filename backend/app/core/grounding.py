"""LLM 输出接地校验（grounding gate）——三类拒绝码。

来源：trading 分组调研采纳的设计（docs/github-llm-agent-audit.md）：
- Vibe-Trading 的反幻觉三类拒绝码（引用不存在数据 / 超出输入范围推断 / 格式越权）
- daily_stock_analysis 的评分-动作一致性守卫——本项目红线 3 全链路不输出买卖动作，
  守卫的对应物是「LLM 研判不得夹带指令性交易建议」（结论强度 ≤ 证据强度的 LLM 侧落点；
  规则引擎侧由 confirm_signal 的 unknown 压强度链路保证，已自洽）

三类拒绝码：
- EVIDENCE_NOT_FOUND      引用不存在数据：输出引用的带单位数字（百分数/家/个/只/板/bp/
                          千分位金额/独立小数）在证据池中找不到——编造或记错指标值
- OUT_OF_SCOPE_INFERENCE  超出输入范围推断：输出含指令性交易建议（"建议买入"“止损位 10.32”、
                          "目标价 X"、“仓位 20%”）——输入是事实底稿场景，操作指令即越权
- SCHEMA_VIOLATION        格式越权：未知 id / 非法字段 / 非法枚举——各消费点已有白名单
                          校验（news 的 _valid_patch、review 的维度 key 过滤），本模块不重复实现

设计原则：
- **宁可漏报不可误杀**：规则判读自己的措辞（"不宜追高""建议控制仓位、减少出手频率"）
  必须放行；被拦的只有"指令性动词+标的/数字"形态。"亿/万"级换算自由度高，不校验。
- **数字比对宽容**：符号不敏感（底稿 -0.50% ↔ 复述"下跌 0.50%"同值）、
  %↔bp 刚性换算互认（0.5% = 50bp）；纯整数不校验（序数与列举误杀高）。
- 校验不过 → 丢弃该条输出并留痕（rejected 数组），**不整体上抛**——逐条粒度比
  整体降级更精细；一条都不合规时由调用方决定回退规则原判。
- 未知 id 类格式越权已有各消费点处理（news 丢弃 unknown id 并 warning、review 丢弃
  未知维度 key 并 warning），符合拒绝码语义，只是留痕位置分散。
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterable

CODE_EVIDENCE_NOT_FOUND = "EVIDENCE_NOT_FOUND"
CODE_OUT_OF_SCOPE_INFERENCE = "OUT_OF_SCOPE_INFERENCE"
CODE_SCHEMA_VIOLATION = "SCHEMA_VIOLATION"

# 指令性交易建议：只抓"明确交易动词"形态。刻意排除的词：出手/追高/抄底/关注/
# 控制（歧义大，规则判读自己也用）——"建议控制仓位""不宜追高""可以关注"放行。
_ADVICE_PATTERNS: tuple[re.Pattern, ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"建议[^。；;\n]{0,8}?(买入|卖出|加仓|减仓|清仓|建仓|补仓|介入|进场|离场)",
        r"(可以|适合|值得|可)[^。；;\n]{0,4}?(买入|卖出|建仓|介入|进场|重仓|满仓)",
        r"(买入|卖出|止盈|止损)(位|价|点)\s*[:：]?\s*[-0-9.]+",
        r"目标价\s*[:：]?\s*[-0-9.]+",
        r"(仓位|持仓)[^。；;\n]{0,4}[0-9]+(?:\.[0-9]+)?%",
        r"(坚决|立即|马上|赶紧|果断|直接)(买入|卖出|进场|离场|加仓|减仓|清仓)",
    )
)
_ADVICE_VERBS = (
    "买入", "卖出", "加仓", "减仓", "清仓", "建仓", "补仓", "介入", "进场", "离场",
)

# 事实性数字引用：千分位金额 / 百分数 / 强离散单位（家|个|只|板|bp）/ 独立小数。
# 刻意不校验：纯整数（序数与列举误杀高）、亿/万（换算自由度高）、日期时间。
_NUMBER_RE = re.compile(
    r"-?[0-9]{1,3}(?:,[0-9]{3})+(?:\.[0-9]+)?"      # 1,297.40
    r"|-?[0-9]+(?:\.[0-9]+)?%"                       # -2.60%
    r"|-?[0-9]+(?:\.[0-9]+)?\s?(?:家|个|只|板|bp|BP)"  # 55 家 / 35bp
    r"|-?[0-9]+\.[0-9]+"                             # 35.7 / 13.2
)
_NUM_PART_RE = re.compile(r"-?[0-9][0-9,]*(?:\.[0-9]+)?")


def _num_values(text: str) -> set[float]:
    """抽取文本中所有数字的字面值（去千分位逗号，含百分号前数值）。

    符号不敏感：同时登记绝对值——底稿 -0.50% 与复述"下跌 0.50%"不算编造
    （涨跌方向由措辞承载，翻转措辞的误杀远大于符号伪造的风险）。
    """
    out: set[float] = set()
    for m in _NUM_PART_RE.finditer(text):
        try:
            v = float(m.group().replace(",", ""))
        except ValueError:
            continue
        out.add(v)
        out.add(abs(v))
    return out


def _token_values(token: str) -> set[float]:
    """单个数字 token 的候选值集合：字面值 + %↔bp 刚性换算（0.5% = 50bp）。

    亿/万等自由换算不在此列——换算自由度高，不校验。
    """
    out = _num_values(token)
    t = token.strip().lower()
    if out and t.endswith("%"):
        out |= {v * 100 for v in list(out)}
    elif out and t.endswith("bp"):
        out |= {v / 100 for v in list(out)}
    return out


def advice_violations(text: str) -> list[str]:
    """指令性交易建议检测。返回命中的模式列表（空 = 通过）。"""
    hits: list[str] = []
    for pat in _ADVICE_PATTERNS:
        m = pat.search(text)
        if m:
            hits.append(m.group())
    return hits


def number_violations(text: str, evidence_texts: Iterable[str]) -> list[str]:
    """数字接地检测：输出中带单位/小数的数字必须能在证据池里找到同值。

    证据池侧取证据全文的所有数字（含纯整数——宁全勿漏，池子越全误杀越少）；
    输出侧只校验带单位/小数的数字（纯整数序数与列举误杀高，放过）。
    容差语义：13.2% 与底稿 13.20% 视为同值（float 归一化）、0.5% 与 50bp
    刚性换算互认；底稿没有的数值（如编造的「涨停 88 家」）→ 拒绝。
    """
    pool_strings: set[str] = set()
    pool_values: set[float] = set()
    for t in evidence_texts:
        if not t:
            continue
        pool_values |= _num_values(t)
        for m in _NUMBER_RE.finditer(t):
            token = m.group().strip()
            pool_strings.add(token)
            pool_values |= _token_values(token)

    misses: list[str] = []
    for m in _NUMBER_RE.finditer(text):
        token = m.group().strip()
        if token in pool_strings:
            continue
        vals = _token_values(token)
        # 候选是同义换算（字面值/绝对值/%↔bp），任一命中即有据——
        # 不能用 issubset：底稿记 50bp 时复述 0.5% 合法，反之亦然
        if vals and (vals & pool_values):
            continue
        misses.append(token)
    return misses


def grounding_violations(text: str, evidence_texts: Iterable[str]) -> list[dict]:
    """合并校验：返回结构化拒绝码列表（空 = 通过）。

    [{code: EVIDENCE_NOT_FOUND|OUT_OF_SCOPE_INFERENCE, detail: ...}, ...]
    """
    out: list[dict] = []
    for hit in advice_violations(text):
        out.append({
            "code": CODE_OUT_OF_SCOPE_INFERENCE,
            "detail": f"指令性建议越权：…{hit}…",
        })
    for token in number_violations(text, evidence_texts):
        out.append({
            "code": CODE_EVIDENCE_NOT_FOUND,
            "detail": f"引用了证据池中不存在的数字：{token}",
        })
    return out


def evidence_pool(*texts: str | None, extra: dict | list | None = None) -> list[str]:
    """便捷构造证据池：若干文本 + 一个可序列化对象（dict/list 转 JSON 文本）。"""
    pool = [t for t in texts if t]
    if extra is not None:
        pool.append(json.dumps(extra, ensure_ascii=False, default=str))
    return pool
