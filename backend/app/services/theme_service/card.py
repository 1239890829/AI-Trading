"""单张题材卡片的构建：连板天梯 / 强度指标 / 龙头与候选（纯计算，零 IO）。

`backend/app/services/theme_service.py` 的内部切片（IMP-005 批 4，2026-09-15 从 1383 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与前置注释与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app.services.theme_service` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

from collections import defaultdict
from app.services.dragon_service import dragon_score, news_persistence, stock_sentiment, theme_core

from .attribution import (
    UNCLASSIFIED,
)
from .board_match import (
    match_board,
)
from .formation import (
    formation_level,
    strength_tier,
    theme_strength_score,
)
from .health import (
    seal_quality_score,
    theme_health_note,
)
from .normalize import (
    early_seal_rate,
    parse_hhmmss,
    parse_theme_tags,
    seal_phase,
    seal_retention_rate,
)
from .roles import (
    MIDDLE_WEIGHT_MIN_CAP,
    ROLE_ORDER,
    classify_role,
    echelon_completeness,
    judge_theme_stage,
)
# ---------------------------------------------------------------- IO 编排


def _median(values: list[float]) -> float | None:
    """中位数；空列表返回 None。均值会被少数极端值拉偏，情绪类指标一律用中位数。"""
    if not values:
        return None
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2


def _build_card(
    *,
    theme: str,
    members: list,
    stock_themes: dict[str, list[str]],
    enhance: dict[str, dict],
    board_index: dict[str, dict],
    prev_boards_map: dict[str, int],
    prev_theme_stats: dict[str, dict],
    market_max_boards: int,
    active_days: int,
    daily_counts: list[tuple[str, int]],
    market_break_rate: float | None,
    snapshot_map: dict[str, dict] | None,
    prev_theme_symbols: set[str],
    auction_gaps: dict[str, float] | None = None,
) -> dict:
    """构建单张题材卡片。"""
    theme_max_boards = max((r.consecutive_boards or 0) for r in members)

    # ---- 连板天梯 ----
    ladder: list[dict] = []
    levels: dict[int, int] = defaultdict(int)
    seal_dist: dict[str, int] = defaultdict(int)
    reopen_count = 0
    turnover_rates: list[float] = []
    amounts: list[float] = []
    caps: list[float] = []
    has_middle_weight = False
    # 封单质量（A2 下半）：早封率样本 / 留存率成对样本
    early_samples: list[int | None] = []
    retention_pairs: list[tuple[float | None, float | None]] = []

    for rec in members:
        boards = rec.consecutive_boards or 0
        levels[boards] += 1
        em = enhance.get(rec.symbol)
        cap = (em.float_market_cap if em else None)
        bc = (em.break_count if em else None) or 0
        if bc > 0:
            reopen_count += 1
        fst = (em.first_seal_time if em else None) or rec.first_seal_time
        phase = seal_phase(fst)
        early_samples.append(parse_hhmmss(fst))
        retention_pairs.append((rec.seal_amount, rec.max_seal_money))
        if phase:
            seal_dist[phase] += 1
        if em and em.turnover_rate:
            turnover_rates.append(em.turnover_rate)
        if em and em.amount:
            amounts.append(em.amount)
        if cap:
            caps.append(cap)
            if cap >= MIDDLE_WEIGHT_MIN_CAP:
                has_middle_weight = True

        role = classify_role(
            boards=boards,
            theme_max_boards=theme_max_boards,
            market_max_boards=market_max_boards,
            prev_boards=prev_boards_map.get(rec.symbol),
            float_market_cap=cap,
            seal_phase_value=phase,
            break_count=bc,
        )
        # 梯队联动归属后，卡片成员就是「主题材为本题材」的股票（assign_primary_themes），
        # 不再有从属行——旧版在这里按 rank_index 判 is_primary，导致同一梯队拆散展示。
        primary = True
        # 龙头前瞻打分 + 个股情绪：在首板/二板阶段就给出偏向，而不是等涨到高位
        # 再用高度倒推（那是结果归因）。第二高判定用于区分前排与跟风。
        second_highest = boards == theme_max_boards - 1 and theme_max_boards >= 3
        dragon = dragon_score(
            boards=boards,
            seal_amount=rec.seal_amount,
            float_market_cap=cap,
            first_seal_time=fst,
            break_count=(em.break_count if em else None),
            turnover_rate=(em.turnover_rate if em else None),
            is_theme_highest=(boards == theme_max_boards and theme_max_boards >= 2),
            is_second_highest=second_highest,
            boards_stat=rec.boards_stat,
            auction_gap_pct=(auction_gaps or {}).get(rec.symbol),
        )
        senti = stock_sentiment(
            boards=boards,
            seal_amount=rec.seal_amount,
            float_market_cap=cap,
            first_seal_time=fst,
            last_seal_time=(em.last_seal_time if em else None) or rec.last_seal_time,
            break_count=(em.break_count if em else None),
            turnover_rate=(em.turnover_rate if em else None),
            is_theme_highest=(boards == theme_max_boards and theme_max_boards >= 2),
            is_primary_theme=bool(primary),
            boards_stat=rec.boards_stat,
        )
        ladder.append(
            {
                "symbol": rec.symbol,
                "name": rec.name,
                "role": role,
                "is_primary": bool(primary),
                "other_themes": [t for t in stock_themes.get(rec.symbol, []) if t != theme],
                "boards": boards,
                "boards_stat": rec.boards_stat,
                "seal_amount": rec.seal_amount,
                "break_count": bc,
                "turnover_rate": (em.turnover_rate if em else None),
                "float_market_cap": cap,
                "amount": (em.amount if em else None),
                "industry_board": (em.industry_board if em else None),
                "first_seal_time": fst,
                "last_seal_time": (em.last_seal_time if em else None) or rec.last_seal_time,
                "seal_phase": phase,
                "change_pct": rec.change_pct,
                "dragon": dragon,
                "sentiment": senti,
                # 同花顺官方涨停原因原串（`+` 分隔的题材串）。必须**在 ladder 行上**导出：
                # 消费方（intraday_opportunity）曾绕道 leaders.candidates 取，而 candidates
                # 只含「补涨/反包」两种角色、值是硬编码文案 ⇒ 绝大多数梯队员取不到、
                # 卡片「入选原因」恒空（2026-09-10 用户反馈）。
                "reason": rec.reason,
            }
        )

    ladder.sort(key=lambda x: (-(x["boards"] or 0), ROLE_ORDER.get(x["role"], 9), -(x["seal_amount"] or 0)))

    # ---- 强度指标 ----
    total = len(members)
    reopen_rate = round(reopen_count / total, 4) if total else 0.0
    completeness = echelon_completeness(dict(levels), theme_max_boards)
    seal_quality = seal_quality_score(dict(seal_dist), sum(seal_dist.values()))

    # 接力赚钱效应：前一日「该题材」涨停股今日涨跌幅中位数。
    # 必须用题材子集而非全市场——全市场口径会让所有题材溢价都等于大盘中位数，
    # 完全失去区分度（曾因此所有题材都显示 1.9%）。
    premium_median: float | None = None
    premium_samples = 0
    if snapshot_map and prev_theme_symbols:
        vals: list[float] = []
        for sym in prev_theme_symbols:
            row = snapshot_map.get(sym)
            if not row:
                continue
            pct = row.get("change_pct")
            if pct is None:
                continue
            vals.append(float(pct))
        if vals:
            premium_median = round(_median(vals) or 0.0, 2)
            premium_samples = len(vals)

    prev_stat = prev_theme_stats.get(theme)
    stage, stage_basis = judge_theme_stage(
        limit_up_count=total,
        max_boards=theme_max_boards,
        prev_limit_up_count=(prev_stat or {}).get("count"),
        prev_max_boards=(prev_stat or {}).get("max_boards"),
        reopen_rate=reopen_rate,
        completeness=completeness,
        premium_median=premium_median,
    )

    score = theme_strength_score(
        limit_up_count=total,
        max_boards=theme_max_boards,
        completeness=completeness,
        reopen_rate=reopen_rate,
        seal_quality=seal_quality,
        active_days=active_days,
    )

    tier, tier_basis = strength_tier(
        formation=formation_level(total),
        stage=stage,
        max_boards=theme_max_boards,
        reopen_rate=reopen_rate,
        premium_median=premium_median,
    )
    sort_basis = (
        f"涨停 {total} 家 · 最高 {theme_max_boards} 板 · 完整度 {completeness:.0%}"
        f" · 开板率 {reopen_rate:.0%} · 活跃 {active_days} 天"
        + (f" · 接力溢价 {premium_median:+.2f}%" if premium_median is not None else "")
    )

    note, risks = theme_health_note(
        theme=theme,
        stage=stage,
        limit_up_count=total,
        max_boards=theme_max_boards,
        completeness=completeness,
        missing_levels=[lv for lv in range(2, theme_max_boards) if (levels.get(lv) or 0) <= 0],
        has_middle_weight=has_middle_weight,
        reopen_rate=reopen_rate,
        premium_median=premium_median,
    )
    formation = formation_level(total)

    board = match_board(theme, board_index)
    ladder_leader = next((r for r in ladder if r["role"] in ("空间板", "龙头")), None)
    middle_weights = [r for r in ladder if r["role"] == "中军"]
    candidates = [r for r in ladder if r["role"] in ("补涨", "反包")]

    # 炒作内核 + 持续性：回答「这个题材在炒什么」和「还值不值得跟」。
    # 位置判据此处先用 active_days 代理；路由层用 ths 官方板块 K 线覆盖 chg_5d 后
    # 会调 apply_position_with_5d 把位置升级为真实区间涨幅（P1-6，见 market.py）。
    reasons = [r.reason for r in members if r.reason]
    core = theme_core(theme, reasons)
    persistence = news_persistence(
        theme=theme,
        core_type=core["type"],
        limit_up_count=total,
        active_days=active_days,
        board_change_pct=(board or {}).get("change_pct"),
        main_net_inflow=(board or {}).get("main_net_inflow"),
        has_second_board=theme_max_boards >= 2,
        reasons=reasons,
    )

    return {
        "theme": theme,
        "raw_tags": sorted({t for r in members for t in parse_theme_tags(r.reason)}) or [],
        "is_unclassified": theme == UNCLASSIFIED,
        "strength_score": score,
        "strength_tier": tier,
        "tier_basis": tier_basis,
        "sort_basis": sort_basis,
        "stage": stage,
        "stage_basis": stage_basis,
        "formation": formation,
        "health_note": note,
        "risks": risks,
        "core": core,
        "persistence": persistence,
        "board": board,
        "board_matched": board is not None,
        "performance": {
            "limit_up_count": total,
            "max_boards": theme_max_boards,
            "echelon_levels": {str(k): v for k, v in sorted(levels.items())},
            "echelon_completeness": completeness,
            "has_succession": theme_max_boards >= 2,
            "reopen_rate": reopen_rate,
            "seal_success_rate": round(1 - reopen_rate, 4),
            "market_break_rate": market_break_rate,
            "seal_time_distribution": dict(seal_dist),
            "seal_quality": seal_quality,
            "early_seal_rate": early_seal_rate(early_samples),
            "seal_retention": seal_retention_rate(retention_pairs),
            "turnover_median": round(_median(turnover_rates), 2) if turnover_rates else None,
            "amount_total": round(sum(amounts), 2) if amounts else None,
            "float_cap_median": round(_median(caps), 2) if caps else None,
            "has_middle_weight": has_middle_weight,
            "active_days": active_days,
            "daily_limit_up_counts": daily_counts,
            "premium_median": premium_median,
            "premium_samples": premium_samples,
        },
        "ladder": ladder,
        "leaders": {
            "main": (
                {
                    "symbol": ladder_leader["symbol"],
                    "name": ladder_leader["name"],
                    "boards": ladder_leader["boards"],
                    "role": ladder_leader["role"],
                }
                if ladder_leader
                else None
            ),
            "middle_weights": [
                {"symbol": r["symbol"], "name": r["name"], "boards": r["boards"]} for r in middle_weights
            ],
            "candidates": [
                {
                    "symbol": r["symbol"],
                    "name": r["name"],
                    "boards": r["boards"],
                    "role": r["role"],
                    "reason": ("连板中断后再度封板" if r["role"] == "反包" else "龙头打出空间后的低位替代"),
                }
                for r in candidates
            ],
        },
    }
