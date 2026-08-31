"""B4（P1-4）：ths 连板天梯 ``seal_nextday`` 交叉验证自算晋级率——数据源自证闭环。

自算口径（``sentiment.engine.promotion_rates``）依赖「昨日池 × 今日池」两日拼接：
board_num 归一、跨日同股配对、任一日池缺口都会静默改变结果。ths 天梯自带每只
连板股的次日封板结果（seal_nextday），等于源方自己算好的晋级结论——两边对不上
就是拼接逻辑有问题，逐日给出 verdict 并在漂移时 log.warning。

可验档位：天梯只有 ≥2 板梯队（首板不在矩阵里），故 **1进2 无法用天梯验证**；
可验 2进3（two_board 档 seal_nextday）与高位存活（board_num ≥ 3 各档合并）。
"""
from __future__ import annotations

import logging
from datetime import date as date_cls

log = logging.getLogger(__name__)

# 两边都是"次日封板/不封板"的硬事实，理论应完全一致；留容差是给盘中抓池的
# 时点差与源方数据修订的余量。超出即 verdict=drift。
TOLERANCE = 0.10
MIN_BASE = 5  # 任一边样本低于此数不判 drift（小样本一票就是 20%）

_TIERS_HIGH_START = 3  # board_num ≥ 3 视为高位


def ladder_rates(rows: list[dict]) -> dict[str, dict[str, tuple[int, int]]]:
    """天梯行 → ``{date: {"p23": (封板数, 基数), "high": (...)}}``（纯函数）。

    seal_nextday 为 None 的行（最近交易日，无次日参考）不计入。
    """
    acc: dict[str, dict[str, list[int]]] = {}
    for r in rows:
        sn = r.get("seal_nextday")
        if sn is None:
            continue
        if r.get("tier") == "two_board":
            key = "p23"
        elif int(r.get("board_num") or 0) >= _TIERS_HIGH_START:
            key = "high"
        else:
            continue
        day = acc.setdefault(r["date"], {"p23": [0, 0], "high": [0, 0]})
        day[key][0] += 1 if sn else 0
        day[key][1] += 1
    return {d: {"p23": tuple(v["p23"]), "high": tuple(v["high"])} for d, v in acc.items()}


async def run_ladder_check(hub, *, max_days: int = 5) -> dict:
    """拉天梯 + 回看两日池 → 逐日对照自算晋级率。

    ths 源不在链上 / 天梯为空 / 池全失败时抛 RuntimeError（路由映射 503/502）。
    次日映射直接用天梯窗口自带的交易日序列（升序后下一元素），天然跳过非交易日。
    """
    from app.sentiment.engine import promotion_rates
    from app.services.theme_service import _pick_provider

    ths = _pick_provider(hub.provider, "ThsFuyaoProvider")
    if ths is None:
        raise RuntimeError("ths provider 不在链上，无法做天梯交叉验证")
    rows = await ths.get_limit_up_ladder()
    rates = ladder_rates(rows)
    if not rates:
        raise RuntimeError("天梯窗口内无可比日（全部 seal_nextday 为 null）")

    dates = sorted(rates.keys())  # 升序；下一元素即次日交易日
    pairs = [(d, dates[i + 1]) for i, d in enumerate(dates) if i + 1 < len(dates)]
    pairs = pairs[-max_days:]

    checks: list[dict] = []
    for d, d_next in pairs:
        pool_d = await ths.get_limit_up_pool(date_cls.fromisoformat(d))
        pool_n = await ths.get_limit_up_pool(date_cls.fromisoformat(d_next))
        promo = promotion_rates(pool_n, pool_d)

        row: dict = {"date": d, "next": d_next}
        for name, ours_key in (("p23", "promo_2to3"), ("high", "high_survival")):
            hit, base = rates[d][name]
            ths_rate = round(hit / base, 3) if base else None
            ours_rate = promo[ours_key]
            ours_base = promo[f"{ours_key}_base"]
            if ths_rate is None or ours_rate is None or base < MIN_BASE or ours_base < MIN_BASE:
                verdict = "insufficient"
            elif abs(ours_rate - ths_rate) <= TOLERANCE:
                verdict = "match"
            else:
                verdict = "drift"
            row[name] = {
                "ours": ours_rate,
                "ours_base": ours_base,
                "ths": ths_rate,
                "ths_hit": hit,
                "ths_base": base,
                "delta": round(ours_rate - ths_rate, 3)
                if ours_rate is not None and ths_rate is not None else None,
                "verdict": verdict,
            }
        checks.append(row)

    drifted = [c["date"] for c in checks if any(c[k]["verdict"] == "drift" for k in ("p23", "high"))]
    if drifted:
        log.warning(
            "ladder check drift on %s：自算晋级率与 ths seal_nextday 不一致，排查两日池拼接口径",
            drifted,
        )
    return {
        "checks": checks,
        "tolerance": TOLERANCE,
        "min_base": MIN_BASE,
        "drifted": drifted,
        "note": "可验档位：2进3（two_board）与高位存活（≥3板合并）；1进2 首板不在天梯，不可验",
    }
