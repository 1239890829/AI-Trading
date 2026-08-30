"""预判引擎：题材成立评分卡 + 梯队推演 + 介入计划（docs/theme-prediction.md §一/§三/§四）。

红线对齐：
- 只输出「预判 + 依据 + 失效条件 + 置信度」，绝不输出确定性结论（AGENTS 红线 3）；
- 评分卡权重显式版本化（ENGINE_VERSION），改权重必须换版本号并记录——
  否则历史命中率统计会失去可比性；
- 成功率是**主观校准区间**（标注来源），被本系统跟踪数据替换之前不承诺精度。
"""
from __future__ import annotations

from app.predict.schemas import (
    EchelonCandidate,
    EntryPlan,
    EvidenceItem,
    ThemePrediction,
)

ENGINE_VERSION = "v1"

# 评分卡权重（v1 初始校准，验证样本积累后按命中率再校准——见 docs §五）
WEIGHTS = {
    "policy_level": 0.20,   # 消息级别（政策权威性与具体度）
    "hot_presence": 0.25,   # 热榜验证（人气先导，最强可量化指标）
    "news_linkage": 0.20,   # 多股新闻联动（题材一致性的数据面证据）
    "freshness": 0.10,      # 题材新鲜度（全新 > 旧题材新催化）
    "environment": 0.15,    # 市场环境适配（冰点/分歧出新，高潮难接力）
    "capital": 0.10,        # 资金验证（龙虎榜游资净买）
}

VERDICT_LEVELS = [(0.72, "预判成立"), (0.58, "可能成立"), (0.42, "弱预期"), (0.0, "不预判")]

# 政策级别关键词（保守清单，宁可漏判不可冒充——识别不出按低级别处理）
POLICY_HIGH = ("国务院", "中央", "中办", "国办", "政治局", "央行", "住建部", "发改委", "财政部", "证监会", "金融监管总局", "中央经济工作")
POLICY_MID = ("部", "委员会", "管理局", "省政府", "市政府", "规划", "意见", "方案", "条例")

# 介入成功率主观校准区间（v1；来源：短线接力公开统计经验区间。
# 待本系统样本外跟踪 ≥20 条后，用实际命中率替换——docs §五）
BASE_SUCCESS = {
    "D1竞价": (0.55, 0.65),
    "D1一字排板": (0.40, 0.50),
    "D1盘中首板": (0.50, 0.60),
    "D2分歧低吸": (0.55, 0.65),
    "D2+补涨轮动": (0.45, 0.55),
    "一字次日追高(禁)": (0.30, 0.40),
}

PHASE_ENV_SCORE = {"冰点": 1.0, "分歧": 0.9, "启动": 0.7, "发酵": 0.7, "高潮": 0.3, "退潮": 0.2}


def _related(candidate: dict, keywords: list[str]) -> bool:
    """候选股与题材的数据面关联：新闻命中 / 概念标签命中 / 名称命中。"""
    if candidate["news_matched"]:
        return True
    if any(any(k in t for k in keywords) for t in candidate.get("tags", [])):
        return True
    name = candidate.get("name") or ""
    return any(k in name for k in keywords)


def _policy_level(evidence_pack: dict, keywords: list[str]) -> tuple[float, EvidenceItem | None]:
    """从命中新闻的标题判定消息级别。识别不出 → 低级别 + 诚实标注。"""
    matched_titles = [t for c in evidence_pack["candidates"] for t in c["news_matched"]]
    for t in matched_titles:
        if any(w in t for w in POLICY_HIGH):
            return 1.0, EvidenceItem(kind="policy_level", source="eastmoney_news", content=f"国家级政策信号：{t}", contribution=WEIGHTS["policy_level"] * 1.0)
    for t in matched_titles:
        if any(w in t for w in POLICY_MID):
            return 0.6, EvidenceItem(kind="policy_level", source="eastmoney_news", content=f"部委/地方级政策信号：{t}", contribution=WEIGHTS["policy_level"] * 0.6)
    if matched_titles:
        return 0.3, EvidenceItem(kind="policy_level", source="eastmoney_news", content=f"仅行业/媒体消息，未识别权威政策源：{matched_titles[0]}", contribution=WEIGHTS["policy_level"] * 0.3)
    return 0.0, None


def judge_theme(hint: str, keywords: list[str], pack: dict, heuristic: bool = False) -> ThemePrediction:
    """对单个题材方向跑评分卡。heuristic=True 为自动发现的主题（置信度封顶）。"""
    evidence: list[EvidenceItem] = []
    gaps: list[str] = list(pack["gaps"])
    env = pack.get("env") or {}
    phase = env.get("phase")

    related = [c for c in pack["candidates"] if _related(c, keywords)]
    related.sort(key=lambda c: c["hot_rank"])

    # ---- 1. 消息级别 ----
    pol_score, pol_ev = _policy_level(pack, keywords)
    if pol_ev:
        evidence.append(pol_ev)
    else:
        gaps.append("policy_level: 候选股新闻未命中题材关键词，消息级别无法判定")
    pol_score = min(pol_score, 0.6) if heuristic else pol_score

    # ---- 2. 热榜验证 ----
    if related:
        top_rank = related[0]["hot_rank"]
        hot_score = 1.0 if top_rank <= 10 else (0.6 if top_rank <= 30 else 0.25)
        names = "、".join(f"{c['name']}(#{c['hot_rank']})" for c in related[:4])
        evidence.append(EvidenceItem(
            kind="hot_presence", source="ths_hot_list",
            content=f"热榜相关标的：{names}（最高第 {top_rank} 位，共 {len(related)} 只）",
            symbol=related[0]["symbol"], contribution=WEIGHTS["hot_presence"] * hot_score,
        ))
    else:
        hot_score = 0.0
        gaps.append("hot_presence: 热榜 top10 无题材关联标的（新闻/标签/名称三路均未命中）")

    # ---- 3. 新闻联动 ----
    matched_stocks = [c for c in related if c["news_matched"]]
    matched_n = len(matched_stocks)
    news_score = min(matched_n / 2.0, 1.0)
    matched_names = "、".join(f"{c['name']}" for c in matched_stocks[:3]) or "?"
    if matched_n >= 2:
        evidence.append(EvidenceItem(
            kind="news_linkage", source="eastmoney_news",
            content=f"{matched_n} 只热榜个股（{matched_names}）新闻命中题材关键词——多股同向发酵",
            symbol=matched_stocks[0]["symbol"], contribution=WEIGHTS["news_linkage"] * news_score,
        ))
    elif matched_n == 1:
        evidence.append(EvidenceItem(
            kind="news_linkage", source="eastmoney_news",
            content=f"仅 {matched_names} 单股新闻命中（如：{matched_stocks[0]['news_matched'][0][:50]}）——个股人气先于板块联动（旗帜标的特征，但梯队未成型）",
            symbol=matched_stocks[0]["symbol"], contribution=WEIGHTS["news_linkage"] * news_score,
        ))

    # ---- 4. 新鲜度 ----
    active_names = {a["theme"] for a in pack.get("active_themes", [])}
    tag_freq = pack.get("theme_tag_freq", {})
    occupied = [t for t in active_names if any(k in t for k in keywords)] + [
        t for t in tag_freq if any(k in t for k in keywords) and tag_freq[t] >= 2
    ]
    if occupied:
        freshness_score = 0.5
        evidence.append(EvidenceItem(
            kind="freshness", source="ths_limit_up",
            content=f"旧题材新催化：近期涨停池/题材卡已含「{'、'.join(sorted(set(occupied))[:3])}」——需更高消息级别补偿",
            contribution=WEIGHTS["freshness"] * 0.5,
        ))
    else:
        freshness_score = 1.0
        evidence.append(EvidenceItem(
            kind="freshness", source="ths_limit_up",
            content="全新方向：近 5 日涨停池与当前题材卡均无该主题——资金饥饿时新题材辨识度最高",
            contribution=WEIGHTS["freshness"] * 1.0,
        ))

    # ---- 5. 市场环境 ----
    env_score = PHASE_ENV_SCORE.get(phase, 0.5)
    if phase is None:
        gaps.append("environment: 情绪阶段未知，按中性 0.5 计")
    evidence.append(EvidenceItem(
        kind="environment", source="market_context",
        content=f"市场情绪阶段：{phase or '未知'}（{'利于新题材接力' if env_score >= 0.7 else '新题材接力难度高' if env_score < 0.5 else '中性'})",
        contribution=WEIGHTS["environment"] * env_score,
    ))

    # ---- 6. 资金验证 ----
    with_dragon = [c for c in related if c.get("dragon_net_buy") is not None and (c["dragon_net_buy"] or 0) > 0]
    if with_dragon:
        capital_score = 1.0
        names = "、".join(f"{c['name']}(净买 {(c['dragon_net_buy'] or 0) / 1e8:.2f} 亿)" for c in with_dragon[:3])
        evidence.append(EvidenceItem(
            kind="capital", source="ths_longhu", content=f"龙虎榜游资净买入：{names}",
            symbol=with_dragon[0]["symbol"], contribution=WEIGHTS["capital"] * 1.0,
        ))
    else:
        capital_score = 0.4
        gaps.append("capital: 候选股未上最近交易日龙虎榜（首板未触发上榜条件属常见，非负面信号）")

    total = (
        WEIGHTS["policy_level"] * pol_score
        + WEIGHTS["hot_presence"] * hot_score
        + WEIGHTS["news_linkage"] * news_score
        + WEIGHTS["freshness"] * freshness_score
        + WEIGHTS["environment"] * env_score
        + WEIGHTS["capital"] * capital_score
    )
    total = round(min(total, 1.0), 3)

    verdict = next(v for th, v in VERDICT_LEVELS if total >= th)
    confidence = (
        "high" if total >= 0.72 and hot_score >= 0.6 and news_score >= 0.5
        else "medium" if total >= 0.58
        else "low"
    )
    if heuristic and confidence == "high":
        confidence = "medium"  # 自动发现的主题不允许 high（无人工确认的消息面）

    ech = deduce_echelon(related)
    leader_name = ech[0].name if ech else "龙头候选"

    return ThemePrediction(
        theme=hint,
        keywords=keywords,
        verdict=verdict,
        score=total,
        confidence=confidence,
        evidence=evidence,
        fail_conditions=_fail_conditions(keywords),
        echelon=ech,
        echelon_note=(
            "梯队推演是入场前的假设清单，不是结论——梯队只能等走出来才能最终确认。"
            "每日收盘后用实际涨停池自动回填对比（见验证机制），偏差本身就是复盘输入。"
        ),
        evolution_path=_evolution_path(leader_name, pack["target_date"]),
        entry_plans=plan_entries(verdict, phase, confidence),
        risks=_risks(phase, verdict),
        data_gaps=gaps,
    )


def deduce_echelon(related: list[dict]) -> list[EchelonCandidate]:
    """梯队推演：人气+联动证据排序 → 龙头/中军/跟风候选。

    推演依据（按可得数据）：热榜排名（人气）、新闻命中数（题材纯度）、
    龙虎榜净买（资金背书）。市值/流通盘/股东结构等关键维度**无数据源**，
    显式声明为局限，不虚构。
    """
    if not related:
        return []

    def _score(c: dict, max_heat: float) -> float:
        rank = c["hot_rank"]
        s = 1.0 - min(rank, 30) / 40.0  # #1→0.975, #30→0.25
        s += 0.15 * min(len(c["news_matched"]), 2)
        if (c.get("dragon_net_buy") or 0) > 0:
            s += 0.10
        if max_heat and c.get("heat"):
            s += 0.10 * (c["heat"] / max_heat)  # 热度值：同榜内相对人气
        return s

    max_heat = max((c.get("heat") or 0) for c in related) or 0
    ranked = sorted(related, key=lambda c: _score(c, max_heat), reverse=True)
    out: list[EchelonCandidate] = []
    for i, c in enumerate(ranked):
        if i == 0:
            role, conf = "龙头候选", "medium"
        elif i <= 2:
            role, conf = "中军候选", "low"
        else:
            role, conf = "跟风候选", "low"
        basis_bits = [f"热榜第 {c['hot_rank']} 位"]
        if c["news_matched"]:
            basis_bits.append(f"新闻命中 {len(c['news_matched'])} 条")
        if c.get("heat"):
            basis_bits.append(f"热度 {c['heat'] / 10000:.0f} 万")
        if c.get("dragon_net_buy") is not None:
            basis_bits.append(f"龙虎榜净买 {(c['dragon_net_buy'] or 0) / 1e8:.2f} 亿")
        out.append(EchelonCandidate(
            symbol=c["symbol"], name=c["name"], role=role, hot_rank=c["hot_rank"],
            basis="；".join(basis_bits), confidence=conf,
        ))
    return out


def _evolution_path(leader_name: str, target_date: str) -> list[str]:
    d1 = _fmt_cn_date(target_date)
    return [
        f"D1（{d1}）辨识日：龙头候选 {leader_name} 一字或大幅高开定高度，跟风股混战，题材辨识",
        "D2 淘汰日：分歧淘汰赛——跟风掉队；龙头分歧转一致（缩量回封）则题材续命，这是最关键观察点",
        "D3 固化日：龙头 3 板确立地位，中军补涨，出现高低切换（做 T 与补涨窗口）",
        "D4+ 扩散或衰败：龙头打开高度则周期延续；若 D2-D3 连续大面则退潮，严禁恋战",
    ]


def _fmt_cn_date(yyyymmdd: str) -> str:
    try:
        from datetime import datetime

        d = datetime.strptime(yyyymmdd, "%Y%m%d")
        return f"{d.month}月{d.day}日"
    except ValueError:
        return yyyymmdd


def _fail_conditions(keywords: list[str]) -> list[str]:
    kw = "/".join(keywords[:3]) or "题材"
    return [
        f"D1 09:25 竞价：候选股无一字板且「{kw}」相关板块高开 ≤2%（一致性不足，直接作废）",
        f"D1 收盘：{kw}题材涨停家数 <3（梯队未成形，降级为个股行情）",
        "D1 热榜：相关标的全部跌出 top50（人气证伪）",
        "消息证伪：官方澄清/权威媒体证实为误读或旧闻重炒",
        "环境失效：D1 大盘转入退潮（跌停家数 > 涨停家数），新题材让位于防守",
    ]


def plan_entries(verdict: str, phase: str | None, confidence: str) -> list[EntryPlan]:
    """介入时点计划。成功率按环境与置信度调整（退潮 -10pp，弱预期 -10pp）。"""
    delta = 0.0
    if phase == "退潮":
        delta -= 0.10
    if verdict == "弱预期":
        delta -= 0.10
    _ = confidence  # v1 未用：置信度进入成功率的调整留待样本外校准（docs §五）

    def _fmt(key: str) -> str:
        lo, hi = BASE_SUCCESS[key]
        lo2, hi2 = max(lo + delta, 0.05), max(hi + delta, 0.10)
        return f"{lo2:.0%}-{hi2:.0%}"

    common_basis = "主观校准区间（短线接力公开统计经验）；本系统样本外跟踪 ≥20 条后由实际命中率替换"
    plans = []
    if verdict in ("预判成立", "可能成立"):
        plans.append(EntryPlan(
            timing="D1竞价", condition="候选龙头高开 3%-6%（非一字）且板块内 ≥3 只高开 >2%",
            action="竞价末段确认后小仓位介入龙头候选，禁止顶一字", est_success=_fmt("D1竞价"),
            basis=f"非一字首日仍存在上车窗口；{common_basis}", risk="高开低走（大盘转弱或消息被证伪），T+1 无法日内止损",
        ))
        plans.append(EntryPlan(
            timing="D1一字排板", condition="仅限龙头候选 + 封单/流通市值比充足 + 热榜前 10 确认",
            action="模拟盘按次日可成交价回测（真实排板需券商通道，模拟撮合对涨停价拒单）",
            est_success=_fmt("D1一字排板"), basis=f"最强一致性但开板即受损；{common_basis}",
            risk="排板被砸；T+1 制度下当日无法止损，本系统模拟撮合对涨跌停价硬拒单",
        ))
    plans.append(EntryPlan(
        timing="D1盘中首板", condition="10:00 前分时量价齐升站稳均价线，板块涨停家数 ≥2",
        action="首板确认逻辑介入非一字候选（弱预期时唯一计划且低仓位）", est_success=_fmt("D1盘中首板"),
        basis=f"首板是梯队萌芽的最低验证门槛；{common_basis}", risk="午后板块轮动退潮，首板炸板",
    ))
    if verdict in ("预判成立", "可能成立"):
        plans.append(EntryPlan(
            timing="D2分歧低吸", condition="龙头首板后 D2 分歧回调 -5%~-8%，缩量回踩均价/昨日分时高点",
            action="分歧转一致前低吸龙头（经典接力点，纪律优先：破位即弃）", est_success=_fmt("D2分歧低吸"),
            basis=f"分歧转一致是梯队续命的核心确认；{common_basis}", risk="分歧转衰败（大面），需要严格止损纪律",
        ))
        plans.append(EntryPlan(
            timing="D2+补涨轮动", condition="龙头确认 3 板后，中军/低位正宗标的首次异动",
            action="高低切换参与补涨轮动，不追已 2 板以上的滞涨股", est_success=_fmt("D2+补涨轮动"),
            basis=f"梯队固化后的扩散逻辑；{common_basis}", risk="龙头断板引发整体退潮，补涨股一日游",
        ))
        plans.append(EntryPlan(
            timing="一字次日追高(禁)", condition="龙头连续一字后的次日任意追高",
            action="禁止——除非缩量加速板且有完整风控，否则视为接最后一棒", est_success=_fmt("一字次日追高(禁)"),
            basis=f"一字后隔日溢价不稳定，赔率极差；{common_basis}", risk="接最后一棒（本计划仅作警示，不建议执行）",
        ))
    return plans


def _risks(phase: str | None, verdict: str) -> list[str]:
    risks = [
        "本预判是概率假设不是结论：一切以 D1 竞价与首板实际走势为准，失效条件触发即作废",
        "梯队推演依赖热榜/新闻/龙虎榜三个数据面，个股市值结构、股东减持、监管风险无数据源覆盖（gap 已标注）",
        "模拟撮合对涨跌停价硬拒单（T+1/费用全配置化）：一字板计划在系统内只能按次日可成交价回测",
    ]
    if phase == "退潮":
        risks.append("当前市场处于退潮期：新题材历史上在退潮期首日溢价中位数显著为负，仓位纪律优先于观点")
    if verdict == "弱预期":
        risks.append("弱预期题材仅保留观察清单：盘面确认（首板+热榜兑现）前不建议任何介入动作")
    return risks
