"""新闻/公告规则摘要器：重要度分级 + 消息面情绪 + 事实摘要 + 关键数字。

设计原则（沿用本项目一贯口径）：
1. **可解释**：每个判定都回传命中词，不看黑箱分数
2. **不臆造**：摘要只能来自原文片段；原文是表格/乱码时宁可回退到标题，绝不润色补全
3. **不给建议**：只输出 重要度 / 情绪 / 事实，禁止延伸到买卖结论
"""
from __future__ import annotations

import re

# ---------------------------------------------------------------- 词表

# 重要度权重：命中即累加，越高越该被优先读
IMPORTANCE_RULES: list[tuple[str, int, str]] = [
    # 业绩与经营（最硬的信息）
    (r"年度报告|半年(度)?报告|季度报告|业绩预告|业绩快报|中报|年报|一季报|三季报", 40, "定期业绩"),
    (r"预增|预减|扭亏|首亏|续亏|略增|略减", 30, "业绩预告类型"),
    # 资本运作
    (r"重大资产重组|重组|收购|要约收购|吸收合并|分拆", 35, "资本运作"),
    (r"定向增发|非公开发行|可转债|配股|增发", 25, "再融资"),
    # 股东行为
    (r"增持|回购|股权激励|员工持股", 25, "股东增持类"),
    (r"减持|大宗交易|质押|解禁", 20, "股东减持类"),
    # 风险与监管（负面但必须高优先级）
    (r"退市|ST|\*ST|立案|调查|处罚|违规|警示函|问询函", 45, "监管与退市风险"),
    (r"诉讼|仲裁|冻结|查封|担保|逾期", 30, "法律与债务风险"),
    # 经营动态
    (r"重大事项|重大进展|重大变动|重大合同", 20, "重大事项"),
    (r"中标|签约|订单|合作|战略协议", 25, "订单与合作"),
    (r"获批|受理|注册|上市|纳入指数|新产品|投产|涨价", 20, "经营利好"),
    (r"停牌|复牌|变更|澄清|异动", 15, "交易状态"),
    # 例行（明确低优先级，避免稀释）
    (r"召开.{0,12}(说明会|股东大会|董事会|监事会)|权益分派实施|变更证券简称", -25, "例行事项"),
]

SENTIMENT_POSITIVE = [
    "预增", "增长", "上涨", "扭亏", "中标", "签约", "突破", "创新高", "回购", "增持",
    "获批", "超预期", "净利润增", "营收增", "分红", "利好", "涨停", "放量上涨",
]
SENTIMENT_NEGATIVE = [
    "预减", "下降", "下滑", "亏损", "减持", "立案", "处罚", "退市", "诉讼", "冻结",
    "违规", "跌停", "不及预期", "问询", "警示", "质押", "解禁", "终止", "不及", "净流出",
]

# 疑似"原始表格 dump"的判据：东财部分 summary 直接塞了行情表，读不了
_TABLE_HINT = re.compile(r"(\d+\.\d+\s+){4,}")
_WS = re.compile(r"[\u3000\s]+")
_SENT_SPLIT = re.compile(r"(?<=[。！？；])")
_PCT = re.compile(r"[-+]?\d+(?:\.\d+)?%")
_AMOUNT = re.compile(r"\d+(?:\.\d+)?(?:亿|万)元?")

DIGEST_MAX = 90


def _clean(text: str | None) -> str:
    """归一化空白。东财正文里大量全角空格（\\u3000），不处理会糊成一片。"""
    if not text:
        return ""
    return _WS.sub(" ", text).strip()


def _looks_like_table(text: str) -> bool:
    """判定正文是否为原始表格 dump。

    实测样例："计算机 688041 海光信息 -0.47 20875.30 918369.62 1.71 电子 301205 …"
    这种内容做摘要毫无意义，必须整体回退到标题。
    """
    if not text:
        return False
    if _TABLE_HINT.search(text):
        return True
    # 数字密度过高（>12%）且几乎没有中文标点 → 认为是表格
    digits = sum(c.isdigit() for c in text)
    if len(text) > 40 and digits / len(text) > 0.12 and "。" not in text:
        return True
    return False


def _truncate_at_sentence(text: str, limit: int = DIGEST_MAX) -> str:
    """按句截断，避免半句话。"""
    if len(text) <= limit:
        return text
    cut = text[:limit]
    # 优先在最后一个句读处收尾
    for sep in ("。", "；", "！", "？"):
        idx = cut.rfind(sep)
        if idx >= limit * 0.5:
            return cut[: idx + 1]
    return cut.rstrip() + "…"


def _extract_numbers(text: str) -> list[str]:
    """抽取关键数字：百分比与金额。顺序保持出现次序，去重。"""
    out: list[str] = []
    for m in _PCT.finditer(text):
        if m.group() not in out:
            out.append(m.group())
    for m in _AMOUNT.finditer(text):
        if m.group() not in out and len(out) < 6:
            out.append(m.group())
    return out[:6]


def _strip_title_prefix(title: str) -> str:
    """公告标题常有"贵州茅台:贵州茅台关于…"的重复前缀，去一层。"""
    parts = re.split(r"[:：]", title, maxsplit=1)
    if len(parts) == 2:
        head, tail = parts[0].strip(), parts[1].strip()
        # 公告标题常见两种冗余：
        #   ① "贵州茅台:贵州茅台关于…" —— 冒号后重复一遍公司名
        #   ② "贵州茅台:关于…"         —— 只有冒号前缀
        # 因此先去掉前缀，再去掉尾部开头重复的公司名
        if head and tail.startswith(head) and len(tail) > len(head) + 8:
            return tail[len(head):].strip()
        # 尾巴太短说明前缀才是正文，别把内容砍没了
        if len(tail) > 8:
            return tail
    return title.strip()


def _grade(score: int) -> str:
    if score >= 40:
        return "高"
    if score >= 15:
        return "中"
    if score <= -10:
        return "低"
    return "普通"


class RulesSummarizer:
    """规则摘要器。无外部依赖，永远可用。"""

    name = "rules"

    def is_available(self) -> bool:
        return True

    def summarize_item(self, item: dict, *, kind: str = "news") -> dict:
        """给单条新闻/公告附加 digest 字段。

        返回 {importance, importance_score, importance_reasons, sentiment,
              sentiment_reasons, digest, numbers}
        """
        title = _clean(item.get("title"))
        body = _clean(item.get("summary"))
        # 公告没有正文，type 字段本身就是重要信息，并入判定文本
        extra = _clean(item.get("type")) if kind == "announcement" else ""
        judge_text = f"{title} {extra}"

        # --- 重要度
        score = 0
        reasons: list[str] = []
        for pattern, weight, label in IMPORTANCE_RULES:
            if re.search(pattern, judge_text):
                score += weight
                reasons.append(f"{label}({weight:+d})")

        # --- 情绪（只看标题+类型，正文噪音太大）
        pos = [w for w in SENTIMENT_POSITIVE if w in judge_text]
        neg = [w for w in SENTIMENT_NEGATIVE if w in judge_text]
        if pos and not neg:
            sentiment, sent_reasons = "偏正面", pos[:4]
        elif neg and not pos:
            sentiment, sent_reasons = "偏负面", neg[:4]
        elif pos and neg:
            sentiment = "分歧"
            sent_reasons = [f"正面:{p}" for p in pos[:2]] + [f"负面:{n}" for n in neg[:2]]
        else:
            sentiment, sent_reasons = "中性", []

        # --- 摘要：正文可用则用正文，否则回退到（去前缀的）标题
        if body and not _looks_like_table(body):
            digest = _truncate_at_sentence(body)
            digest_source = "正文"
        else:
            digest = _truncate_at_sentence(_strip_title_prefix(title), DIGEST_MAX)
            digest_source = "标题" if not body else "标题（正文为表格数据，已丢弃）"

        return {
            "importance": _grade(score),
            "importance_score": score,
            "importance_reasons": reasons,
            "sentiment": sentiment,
            "sentiment_reasons": sent_reasons,
            "digest": digest,
            "digest_source": digest_source,
            "numbers": _extract_numbers(f"{title} {body}")[:6],
        }

    def summarize(self, news: list[dict], announcements: list[dict]) -> dict:
        """批量摘要，并按重要度排序。"""
        def enrich(items: list[dict], kind: str) -> list[dict]:
            out = []
            for it in items:
                merged = {**it, **self.summarize_item(it, kind=kind)}
                out.append(merged)
            out.sort(
                key=lambda r: (r["importance_score"], r.get("date") or ""),
                reverse=True,
            )
            return out

        return {"news": enrich(news, "news"), "announcements": enrich(announcements, "announcement")}
