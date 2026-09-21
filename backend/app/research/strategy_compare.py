"""IMP-020 ablation and Champion/Challenger research comparison evidence.

All comparisons are forced onto one table/window/horizon/cost identity.  The module
produces evidence only; it never promotes a strategy and reference close-to-close
results can never stand in for actual shadow-fill net returns.
"""
from __future__ import annotations

from typing import Mapping

from app.research import strategy_verify as sv
from app.research.strategy_trials import signal_overlap_evidence

EVIDENCE_VERSION = 1


def _neutral_distribution(con, cond: str, where: str, *, cfg: sv.VerifyConfig, horizon: int) -> dict:
    cost_pct = float(cfg.cost_bps) / 100.0
    table = cfg.table
    n, med, win = con.execute(
        f"""
        SELECT count(*),
               median(fwd{horizon} - mfwd{horizon} - {cost_pct}),
               avg(CASE WHEN fwd{horizon} - mfwd{horizon} - {cost_pct} > 0
                        THEN 1.0 ELSE 0.0 END)
        FROM {table}
        WHERE ({where}) AND ({cond})
          AND fwd{horizon} IS NOT NULL AND mfwd{horizon} IS NOT NULL
        """
    ).fetchone()
    return {
        "n_neutral": int(n or 0),
        "excess_median": None if med is None else round(float(med), 6),
        "excess_win_rate": None if win is None else round(float(win), 8),
    }


def condition_metrics(con, cond: str, where: str, *, cfg: sv.VerifyConfig, horizon: int) -> dict:
    """One comparable metric bundle under a single frozen basis."""
    row = sv.baseline(
        con, where=f"({where}) AND ({cond})", cfg=cfg, horizons=[horizon]
    )
    summary = sv.summarize_row(row, horizon=horizon, cost_bps=cfg.cost_bps)
    neutral = _neutral_distribution(con, cond, where, cfg=cfg, horizon=horizon)
    limit = sv.limit_up_share(con, f"({where}) AND ({cond})", cfg=cfg)
    return {
        **summary,
        **neutral,
        "limit_up_share": limit.get("limit_up_share"),
    }


def dataset_fingerprint(con, where: str, *, table: str = "sigv") -> dict:
    n, days, lo, hi = con.execute(
        f"SELECT count(*), count(distinct date_ms), min(date_ms), max(date_ms) "
        f"FROM {table} WHERE ({where})"
    ).fetchone()
    return {
        "table": table,
        "rows": int(n or 0),
        "trade_days": int(days or 0),
        "min_date_ms": int(lo) if lo is not None else None,
        "max_date_ms": int(hi) if hi is not None else None,
    }


def leave_one_out_ablation(
    con,
    *,
    label: str,
    components: Mapping[str, str],
    where: str,
    cfg: sv.VerifyConfig,
    horizon: int,
) -> dict:
    """Remove one declared component at a time while freezing every other basis."""
    items = [(str(k), str(v)) for k, v in components.items() if str(k) and str(v)]
    if len(items) < 2:
        raise ValueError("消融至少需要两个具名组件")
    full_cond = " AND ".join(f"({cond})" for _, cond in items)
    full = condition_metrics(con, full_cond, where, cfg=cfg, horizon=horizon)
    rows: list[dict] = []
    for removed, _ in items:
        kept = [(name, cond) for name, cond in items if name != removed]
        cond = " AND ".join(f"({part})" for _, part in kept) or "TRUE"
        metrics = condition_metrics(con, cond, where, cfg=cfg, horizon=horizon)
        full_ex = full.get("excess")
        alt_ex = metrics.get("excess")
        marginal = None if full_ex is None or alt_ex is None else round(float(full_ex) - float(alt_ex), 6)
        rows.append({
            "removed": removed,
            "remaining_condition": cond,
            "metrics": metrics,
            # positive => keeping the removed component improved neutral excess
            "marginal_excess_pp": marginal,
        })
    return {
        "evidence_version": EVIDENCE_VERSION,
        "kind": "leave_one_component_out",
        "label": label,
        "components": dict(items),
        "full_condition": full_cond,
        "where": where,
        "horizon": horizon,
        "cost_bps": float(cfg.cost_bps),
        "return_identity": sv.RETURN_IDENTITY_REFERENCE_PROXY,
        "dataset": dataset_fingerprint(con, where, table=cfg.table),
        "full": full,
        "leave_one_out": rows,
        "review_required": True,
        "production_promotion_eligible": False,
    }


def champion_challenger_evidence(
    con,
    *,
    champion_label: str,
    champion_cond: str,
    challenger_label: str,
    challenger_cond: str,
    where: str,
    cfg: sv.VerifyConfig,
    horizon: int,
    champion_is_production: bool,
) -> dict:
    """Same-basis comparison without silently replacing the incumbent identity."""
    if not champion_label or not challenger_label or champion_label == challenger_label:
        raise ValueError("Champion/Challenger 必须是两个不同具名身份")
    champion = condition_metrics(con, champion_cond, where, cfg=cfg, horizon=horizon)
    challenger = condition_metrics(con, challenger_cond, where, cfg=cfg, horizon=horizon)
    overlap = signal_overlap_evidence(
        con,
        target_label=challenger_label,
        target_cond=challenger_cond,
        incumbents={champion_label: champion_cond},
        where=where,
        table=cfg.table,
    )
    c_ex, h_ex = champion.get("excess"), challenger.get("excess")
    delta = None if c_ex is None or h_ex is None else round(float(h_ex) - float(c_ex), 6)
    return {
        "evidence_version": EVIDENCE_VERSION,
        "kind": "champion_challenger_same_basis",
        "champion": {"label": champion_label, "condition": champion_cond, "metrics": champion},
        "challenger": {"label": challenger_label, "condition": challenger_cond, "metrics": challenger},
        "where": where,
        "horizon": horizon,
        "cost_bps": float(cfg.cost_bps),
        "return_identity": sv.RETURN_IDENTITY_REFERENCE_PROXY,
        "dataset": dataset_fingerprint(con, where, table=cfg.table),
        "challenger_minus_champion_excess_pp": delta,
        "signal_overlap": overlap,
        "same_basis": True,
        "champion_is_production": bool(champion_is_production),
        # Reference research comparison is never itself a production-promotion basis.
        "promotion_basis_eligible": False,
        "review_required": True,
    }
