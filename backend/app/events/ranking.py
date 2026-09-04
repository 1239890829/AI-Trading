"""事件排序（2026-09-04 用户需求）：与当日大盘盘面/情绪强关联，排序依据可解释、可追溯。

核心思想：**能解释今天盘面的事件才值得排前面**。纯时间排序 = 流水账；
本模块给每条事件算一个「盘面相关性分」，且每一分都能说出出处：

1. 题材共振（25 分）：事件方向命中的官方题材，其成分股今日等权涨幅——
   题材今天真的在动，事件才是「现在的事」，否则是过期新闻。
   行情 = 官方成分批量快照（与 /api/themes/catalog/strength 同源同口径）。
2. 个股联动（10 分）：方向个股今日涨幅均值。
3. 大盘/情绪相位（乘数 0.6~1.5）：情绪相位决定「什么事件此刻值钱」——
   冰点/退潮期政策类加权（政策底逻辑）、热点类降权（退潮期热点是陷阱）；
   发酵/高潮期热点/材料加权。相位缺失 → ×1.0（三态，不臆造）。
4. 影响力基线（45/27/9 分）：L1/L2/L3（复用 impact.py 分级）。
5. 时效（10 分）：半衰期 × 2 口径线性衰减（与 is_active 同口径）。
6. 来源（10 分）：source_tier/5。

全部因子缺失（快照/情绪不可用）→ 显式降级为「影响力+时效」排序，
reasons 里注明「盘面上下文不可用」，绝不静默冒充有共振。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

log = logging.getLogger(__name__)

#: 情绪相位 → 相位组（冰点/退潮=冷、发酵/高潮=热、修复、分歧）
_PHASE_GROUP = {
    "冰点": "cold",
    "退潮": "cold",
    "修复": "repair",
    "发酵": "hot",
    "高潮": "hot",
    "分歧": "diverge",
}

#: (相位组, 四分类) → 乘数。missing 相位/分类 → 1.0（三态）。
PHASE_WEIGHTS: dict[tuple[str, str], float] = {
    # 冷市场：政策底/外部宏观是主逻辑，纯热点接力是陷阱
    ("cold", "policy"): 1.5,
    ("cold", "international"): 1.3,
    ("cold", "material"): 1.2,
    ("cold", "hot"): 0.7,
    # 热市场：题材/材料顺势放大，宏观类让位
    ("hot", "hot"): 1.5,
    ("hot", "material"): 1.3,
    ("hot", "policy"): 1.0,
    ("hot", "international"): 0.9,
    # 分歧：政策/材料托底，热点打折
    ("diverge", "policy"): 1.2,
    ("diverge", "material"): 1.1,
    ("diverge", "hot"): 0.9,
    ("diverge", "international"): 0.9,
    # 修复：热点温和加权
    ("repair", "hot"): 1.2,
    ("repair", "policy"): 1.1,
    ("repair", "material"): 1.05,
}

#: 影响力基线分
_IMPACT_BASE = {"L1": 45.0, "L2": 27.0, "L3": 9.0}

LEVEL_REASON = {"L1": "影响力 L1（政策/行业级/国际重大）", "L2": "影响力 L2（事实+有标的链）",
                "L3": "影响力 L3（日常类）"}

FOUR_CN = {"policy": "政策类", "international": "国际类", "material": "材料类", "hot": "热点类"}


def phase_weight(four: str | None, phase: str | None) -> float:
    """相位乘数：查表；任一缺失 → 1.0（不臆造加权）。"""
    if not four or not phase:
        return 1.0
    return PHASE_WEIGHTS.get((_PHASE_GROUP.get(phase, ""), four), 1.0)


def _norm_pct(value: float | None, full_scale: float) -> float | None:
    """涨幅 → [0,1] 贡献度：±full_scale 涨幅打满，负向同尺度（0 分）。None → None。"""
    if value is None:
        return None
    n = (value / full_scale) * 0.5 + 0.5
    return max(0.0, min(1.0, n))


@dataclass
class RankContext:
    """排序上下文：collect_rank_context 的产物；任何字段缺失 = 该因子不参与计分。"""

    phase: str | None = None
    index_chg: float | None = None  # 上证指数今日涨跌幅 %
    theme_perf: dict[str, float] = field(default_factory=dict)  # 题材名 → 成分等权涨幅 %
    stock_chg: dict[str, float] = field(default_factory=dict)  # symbol → 今日涨幅 %
    degraded: list[str] = field(default_factory=list)  # 显式降级说明


def score_event(
    *,
    impact_level: str,
    four: str,
    source_tier: int | None,
    published_at: datetime | None,
    half_life_hours: float | None,
    theme_names: list[str],
    symbol_chg: list[float | None],
    ctx: RankContext,
    now: datetime,
) -> dict:
    """单事件打分。返回 {score, reasons, factors}——reasons 人类可读，factors 可追溯。"""
    reasons: list[str] = []
    factors: dict = {}

    # 1) 题材共振（25）
    theme_scores = {n: _norm_pct(ctx.theme_perf.get(n), 5.0) for n in theme_names}
    theme_scores = {n: s for n, s in theme_scores.items() if s is not None}
    if theme_scores:
        best_name = max(theme_scores, key=lambda n: theme_scores[n])
        best = theme_scores[best_name]
        chg = ctx.theme_perf[best_name]
        reasons.append(f"题材共振：{best_name} 成分今日等权 {chg:+.1f}%")
        factors["theme_best"] = {"name": best_name, "chg_pct": chg}
    else:
        best = None
    theme_pts = 25.0 * best if best is not None else 0.0

    # 2) 个股联动（10）
    vals = [v for v in symbol_chg if v is not None]
    stock_n = _norm_pct(sum(vals) / len(vals) if vals else None, 4.0)
    if stock_n is not None and vals:
        mean_v = sum(vals) / len(vals)
        reasons.append(f"关联个股今日均值 {mean_v:+.1f}%")
        factors["stock_mean_pct"] = mean_v
    stock_pts = 10.0 * stock_n if stock_n is not None else 0.0

    # 3) 影响力基线（45/27/9）
    base = _IMPACT_BASE.get(impact_level, 0.0)
    reasons.append(LEVEL_REASON.get(impact_level, f"影响力 {impact_level}"))

    # 4) 时效（10）：半衰期×2 线性衰减（与 is_active 同口径）
    fresh_n: float | None = None
    if published_at is not None:
        hl = (half_life_hours or 24.0) * 2.0
        age_h = max(0.0, (now - published_at).total_seconds() / 3600.0)
        fresh_n = max(0.0, 1.0 - age_h / hl) if hl > 0 else 0.0
        reasons.append(f"时效：{age_h:.1f} 小时前（半衰期 {hl:.0f}h）")
        factors["age_hours"] = round(age_h, 2)
    else:
        reasons.append("时效：发布时间未知（不计时效分）")
    fresh_pts = 10.0 * (fresh_n or 0.0)

    # 5) 来源（10）
    tier = source_tier or 0
    source_pts = 10.0 * min(tier, 5) / 5.0
    reasons.append(f"来源 tier{tier}/5")

    # 6) 相位乘数
    w = phase_weight(four, ctx.phase)
    if ctx.phase:
        reasons.append(f"相位加权：{ctx.phase}期{FOUR_CN.get(four, four)} ×{w:.2f}")
        factors["phase"] = ctx.phase
        factors["phase_weight"] = w

    subtotal = base + theme_pts + stock_pts + fresh_pts + source_pts
    score = min(100.0, subtotal * w)

    # 降级说明：盘面因子全部缺席时显式告知，不冒充「共振 0 分」
    if not theme_scores and not vals and ctx.phase is None:
        reasons.insert(0, "盘面上下文不可用：按影响力/时效/来源排序")
        if ctx.degraded:
            reasons.insert(0, f"（{'；'.join(ctx.degraded)}）")

    factors["subtotal"] = round(subtotal, 1)
    return {"score": round(score, 1), "reasons": reasons, "factors": factors}


async def collect_rank_context(app_state, theme_names: list[str],
                               symbols: list[str]) -> RankContext:
    """构建排序上下文；每一环失败都显式降级记入 degraded，绝不抛异常、不臆造。

    - 结果 60s TTL 缓存（事件页轮询消费，避免每轮都打腾讯批量快照触发熔断）
    - 情绪相位：复用 /api/market/sentiment 的 60s 缓存（同口径不重复计算）
    - 上证涨幅：hub 指数兜底快照
    - 题材/个股涨幅：官方成分 + 方向个股 腾讯批量快照（50/批，≤400 只）
    """
    from app.core.ttl_cache import cache_on

    cache = cache_on(app_state, "events.rank_context", 60, maxsize=2)
    key = (",".join(sorted(theme_names)) + "|" + ",".join(sorted(symbols)))[:512]
    hit, cached = cache.get(key)
    if hit and cached is not None:
        return cached

    ctx = RankContext()
    hub = getattr(app_state, "hub", None)
    if hub is None:
        ctx.degraded.append("行情服务不可用")

    # 情绪（60s 缓存；miss 时与 /api/market/sentiment 同口径构建一次，不重复造轮子）
    try:
        from app.core.ttl_cache import cache_on
        from app.services.market_context import compute_market_sentiment

        cache = cache_on(app_state, "market.sentiment", 60, maxsize=1)

        async def _build() -> dict:
            result = await compute_market_sentiment(app_state.hub, app_state.snapshot_service)
            return {"data": result, "meta": {}}

        _, payload = await cache.get_or_set((), _build)
        if payload:
            ctx.phase = (payload.get("data") or {}).get("phase")
    except Exception as exc:  # noqa: BLE001
        ctx.degraded.append(f"情绪不可用({type(exc).__name__})")

    # 上证涨幅
    if hub is not None:
        try:
            for q in hub.get_quotes(["sh000001"]) or []:
                if q.symbol.endswith("000001") and q.change_pct is not None:
                    ctx.index_chg = float(q.change_pct)
        except Exception as exc:  # noqa: BLE001
            ctx.degraded.append(f"指数不可用({type(exc).__name__})")

    # 题材成分：名字 → 官方代码 → 成员（内存读）
    svc = getattr(app_state, "theme_catalog", None)
    theme_members: dict[str, list[str]] = {}
    if svc is not None and theme_names:
        try:
            name_to_code = {t.name: t.code for t in svc.get_catalog(limit=1000)}
            for n in theme_names[:12]:  # 有界
                code = name_to_code.get(n)
                if not code:
                    continue
                members = [m.symbol for m in svc.get_members(code)][:200]
                if members:
                    theme_members[n] = members
        except Exception as exc:  # noqa: BLE001
            ctx.degraded.append(f"题材成分不可用({type(exc).__name__})")

    # 批量快照（腾讯，与题材合力/涨速同链路）
    all_symbols = sorted({s for members in theme_members.values() for s in members}
                         | set(symbols[:60]))
    quotes_raw: dict[str, dict] = {}
    if all_symbols and hub is not None:
        composite = hub.provider if hasattr(hub.provider, "providers") else None
        target = next(
            (p for p in (composite.providers if composite else [hub.provider]) if p.name == "tencent"),
            hub.provider,
        )
        for i in range(0, min(len(all_symbols), 400), 50):
            batch = all_symbols[i : i + 50]
            try:
                for q in await target.get_quotes(batch):
                    quotes_raw[q.symbol] = {"change_pct": q.change_pct}
            except Exception as exc:
                log.warning("rank context batch %s failed: %s", i // 50, exc)
        if not quotes_raw:
            ctx.degraded.append("快照不可用")
    if quotes_raw:
        ctx.stock_chg = {
            s: v["change_pct"] for s, v in quotes_raw.items() if v.get("change_pct") is not None
        }

    # 题材等权涨幅（复用合力聚合，纯函数）
    if theme_members and quotes_raw:
        from app.services.theme_catalog_service import aggregate_theme_strength

        strength = aggregate_theme_strength(theme_members, quotes_raw)
        for name, s in strength.items():
            avg = s.get("avg_change_pct")
            if avg is not None:
                ctx.theme_perf[name] = float(avg)

    # 快照整体失败（如腾讯熔断冷启动）→ 不缓存本轮降级结果，下个请求立即重试；
    # 否则一次瞬时失败会被 60s 缓存粘住，事件页整轮排序都缺共振因子。
    if all_symbols and not quotes_raw:
        return ctx
    cache.set(key, ctx)
    return ctx
