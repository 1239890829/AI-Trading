"""预判验证回填：目标日收盘后对 pending 预判跑「验证四问」（docs §五）。

验证四问（每条题材预判在目标日收盘后回答）：
1. 题材成立了吗？——目标日涨停池中题材关联家数 ≥3
2. 人气兑现了吗？——候选股是否还在历史热榜 top50
3. 梯队对了吗？——实际最高连板股 vs 预判龙头候选
4. 结论：hit（题材+龙头全中）/ partial（题材中、龙头偏）/ miss（未成立）

验证结果回填 prediction_themes.verify_outcome —— 这是命中率统计
与方法论再校准（哪类消息级别/环境下预判最准）的直接数据来源。

⚠️ 预判的「生产入口」（采集→评分→落库，原 run_prediction）已于 2026-09-13
死代码清理中移除：全仓（路由/调度/测试）无任何调用方。本模块保留验证回填侧
（verify_predictions / maybe_auto_verify），对库内存量 pending 报告仍然有效；
若将来重启预判生产，从 git 历史找回原实现。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.predict.storage import apply_verify, get_report
from app.services.theme_service import parse_theme_tags

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def verify_predictions(
    hub,
    snapshot_service,
    session_factory,
    target_date: str,
) -> dict | None:
    """目标日收盘后验证：对 targeting 该日且 pending 的预判跑四问。"""
    report = get_report(session_factory, target_date)
    if not report:
        return None
    if report.verify:
        return report.verify  # 已验证过，幂等

    provider = hub.provider
    d = datetime.strptime(target_date, "%Y%m%d").date()

    pool = []
    try:
        pool = await provider.get_limit_up_pool(d)
    except Exception as exc:
        log.warning("verify: limit-up pool failed for %s: %s", target_date, exc)
    try:
        hot_d1 = await provider.get_hot_stock_list_history(d)
    except Exception as exc:
        log.warning("verify: hot history failed for %s: %s", target_date, exc)
        hot_d1 = []

    # 竞价验证数据面：D1 风向标基准 + 候选股竞价终态（D1 09:25 的一致性判定）
    bench_d1: list[dict] = []
    try:
        bench_d1 = await provider.get_auction_benchmark(d)
    except Exception as exc:
        log.warning("verify: auction benchmark failed for %s: %s", target_date, exc)
    auction_map: dict[str, dict] = {}
    echelon_syms = sorted({c.symbol for p in report.predictions for c in p.echelon})
    if echelon_syms:
        try:
            rows = await provider.get_auction_snapshot(echelon_syms, stage="final")
            auction_map = {r["symbol"]: r for r in rows}
        except Exception as exc:
            log.warning("verify: auction snapshot failed for %s: %s", target_date, exc)

    verify: dict = {"verified_at": _now_iso(), "themes": [], "note": ""}
    for p in report.predictions:
        if p.verdict == "不预判":
            continue
        kws = p.keywords
        theme_stocks = []
        for rec in pool:
            reason = rec.reason or ""
            tags = parse_theme_tags(reason)
            hit = any(k in reason for k in kws) or any(any(k in t for k in kws) for t in tags)
            if hit:
                theme_stocks.append(rec)
        formed = len(theme_stocks) >= 3
        leader_actual = None
        if theme_stocks:
            best = max(theme_stocks, key=lambda r: (r.consecutive_boards or 0))
            leader_actual = f"{best.symbol} {best.name or ''} {best.consecutive_boards or 1}板"
        leader_hit = bool(p.echelon) and leader_actual is not None and leader_actual.startswith(p.echelon[0].symbol)
        hot_symbols = {h["symbol"] for h in hot_d1 if h["rank"] <= 50}
        hot_kept = [c for c in p.echelon if c.symbol in hot_symbols]

        if not pool:
            outcome, note = "expired", "涨停池数据缺失，无法验证（不判错也不判对）"
        elif formed and leader_hit:
            outcome, note = "hit", f"题材成立（{len(theme_stocks)} 只涨停）且龙头命中 {leader_actual}"
        elif formed:
            outcome, note = "partial", f"题材成立（{len(theme_stocks)} 只涨停）但实际龙头 {leader_actual} 与预判候选不一致"
        else:
            outcome, note = "miss", f"题材未成立：目标日关联涨停 {len(theme_stocks)} 只（<3）"

        # 竞价一致性（失效条件 #1 的自动核对）：候选最高竞价涨幅 / 一字判定 / 风向标联动
        auction_note = ""
        if pool and auction_map:
            cand_auctions = [(c, auction_map.get(c.symbol)) for c in p.echelon]
            with_a = [(c, a) for c, a in cand_auctions if a and a.get("auction_pct") is not None]
            if with_a:
                best_c, best_a = max(with_a, key=lambda x: x[1]["auction_pct"] or 0)
                best_pct = best_a["auction_pct"] or 0.0
                bits = [f"候选最高竞价 {best_c.name} {best_pct:+.2f}%"]
                if best_pct >= 9.8:
                    bits.append("≈一字/顶格开盘（近似口径 |auction_pct|≥9.8）")
                elif best_pct < 2:
                    bits.append("竞价一致性不足——失效条件 #1 命中")
                bench_hits = [
                    b for b in bench_d1
                    if any(k in b.get("name", "") or k in " ".join(b.get("tags") or []) for k in kws)
                ]
                if bench_hits:
                    names = "、".join(f"{b['name']}({b['auction_pct']:+.1f}%)" for b in bench_hits[:3])
                    bits.append(f"风向标竞价联动 {len(bench_hits)} 只：{names}")
                auction_note = "竞价验证：" + "；".join(bits)
            elif cand_auctions:
                auction_note = "竞价验证：候选股竞价数据未就绪（停牌或源缺失）"

        verify["themes"].append({
            "theme": p.theme,
            "verdict": p.verdict,
            "outcome": outcome,
            "formed": formed,
            "limit_up_count": len(theme_stocks),
            "leader_actual": leader_actual,
            "leader_hit": leader_hit,
            "hot_kept_top50": [f"{c.name}({c.symbol})" for c in hot_kept],
            "note": note,
            "auction_note": auction_note,
        })

    verify["note"] = (
        "四问验证：题材成立(涨停家数≥3) / 人气兑现(热榜top50) / 梯队对照(实际龙头 vs 预判候选)。"
        "outcome 按层统计进入命中率看板，用于权重再校准。"
    )
    apply_verify(session_factory, target_date, verify)
    log.info("prediction verified: target=%s outcomes=%s", target_date,
             [t["outcome"] for t in verify["themes"]])
    return verify


async def maybe_auto_verify(hub, snapshot_service, session_factory, trade_date) -> str | None:
    """复盘 Agent 钩子：若存在针对 trade_date 的 pending 预判，自动验证。
    返回一行摘要（供复盘报告 summary 引用），无预判返回 None。"""
    try:
        from datetime import date as _date

        td = trade_date if isinstance(trade_date, _date) else datetime.strptime(str(trade_date), "%Y%m%d").date()
        target = td.strftime("%Y%m%d")
        report = get_report(session_factory, target)
        if not report or report.verify:
            return None
        verify = await verify_predictions(hub, snapshot_service, session_factory, target)
        if not verify:
            return None
        outcomes = "；".join(f"{t['theme']}:{t['outcome']}" for t in verify["themes"])
        return f"题材预判验证（目标 {target}）：{outcomes or '无可验证项'}"
    except Exception as exc:
        log.warning("predict auto-verify failed: %s", exc)
        return None
