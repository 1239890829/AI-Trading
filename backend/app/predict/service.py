"""预判编排：采集 → 评分 → 报告落库；目标日收盘后验证回填（docs §五）。

验证四问（每条题材预判在目标日收盘后回答）：
1. 题材成立了吗？——目标日涨停池中题材关联家数 ≥3
2. 人气兑现了吗？——候选股是否还在历史热榜 top50
3. 梯队对了吗？——实际最高连板股 vs 预判龙头候选
4. 结论：hit（题材+龙头全中）/ partial（题材中、龙头偏）/ miss（未成立）

验证结果回填 prediction_themes.verify_outcome —— 这是命中率统计
与方法论再校准（哪类消息级别/环境下预判最准）的直接数据来源。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from app.predict.collector import collect_predict_evidence
from app.predict.engine import ENGINE_VERSION, judge_theme
from app.predict.schemas import PredictionReport
from app.predict.storage import apply_verify, get_report, save_report
from app.services.theme_service import parse_theme_tags

log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def run_prediction(
    hub,
    snapshot_service,
    session_factory,
    theme_hint: str | None = None,
    keywords: list[str] | None = None,
    trigger: str = "manual",
) -> PredictionReport:
    """跑一次预判。theme_hint + keywords 给定走定向模式；否则自动发现候选主题。"""
    hint_mode = bool(theme_hint and keywords)
    if not hint_mode:
        # 自动发现：无关键词可先只采热榜与新闻样本，按新闻高频词聚类（v1 启发式）
        kw = keywords or []
        pack = await collect_predict_evidence(hub, snapshot_service, kw)
        discovered = _discover_themes(pack)
        if not discovered:
            pack["gaps"].append("auto_discover: 热榜个股新闻无 ≥2 股共享关键词，未发现候选主题——建议给 theme_hint 定向预判")
            report = _empty_report(pack, trigger)
            report.prediction_id = f"PR-{report.target_date}-{datetime.now().strftime('%H%M%S')}"
            return save_report(session_factory, report)
        predictions = [
            judge_theme(theme, kws, pack, heuristic=True) for theme, kws in discovered
        ]
        used_pack = pack
    else:
        pack = await collect_predict_evidence(hub, snapshot_service, keywords)
        predictions = [judge_theme(theme_hint, keywords, pack)]
        used_pack = pack

    report = PredictionReport(
        created_at=_now_iso(),
        context=used_pack["context"],
        target_date=used_pack["target_date"],
        trigger=trigger,
        engine_version=ENGINE_VERSION,
        market_env={
            "phase": (used_pack.get("env") or {}).get("phase"),
            "last_trade_date": used_pack["last_trade_date"],
            "hot_list_top5": [
                {"rank": h["rank"], "name": h.get("name"), "symbol": h["symbol"]}
                for h in used_pack["hot_list"][:5]
            ],
        },
        predictions=predictions,
        summary=_summary_line(predictions, used_pack),
    )
    report.prediction_id = f"PR-{report.target_date}-{datetime.now().strftime('%H%M%S')}"
    # 报告级 gap（采集层共用），逐题材 gap 已在各 ThemePrediction.data_gaps
    return save_report(session_factory, report)


def _discover_themes(pack: dict) -> list[tuple[str, list[str]]]:
    """无 hint 时从热榜个股新闻标题聚类候选主题（启发式，verdict 封顶可能成立）。"""
    from collections import Counter

    stop = {"的", "了", "在", "与", "和", "将", "为", "上", "下", "中国", "公司", "集团", "公告", "新闻", " regarding"}
    freq: Counter = Counter()
    for c in pack["candidates"]:
        titles = set(c.get("news_sample") or [])
        words: set[str] = set()
        for t in titles:
            buf = []
            for ch in t:
                if "\u4e00" <= ch <= "\u9fff":
                    buf.append(ch)
                else:
                    if buf:
                        words.update(_ngrams("".join(buf)))
                        buf = []
            if buf:
                words.update(_ngrams("".join(buf)))
        for w in words:
            if len(w) >= 2 and w not in stop:
                freq[w] += 1
    # 出现在 ≥2 只不同热榜股新闻里的词 = 潜在共同主题
    common = [w for w, n in freq.items() if n >= 2]
    common.sort(key=lambda w: -freq[w])
    out = []
    for w in common[:5]:
        kws = [w]
        # 同族扩展：包含该词的更长高频词
        kws.extend(x for x in common if w in x and x != w)[:3]
        out.append((w, kws[:4]))
    return out


def _ngrams(text: str, n: tuple[int, ...] = (2, 3, 4)) -> list[str]:
    return [text[i: i + k] for k in n for i in range(len(text) - k + 1)]


def _empty_report(pack: dict, trigger: str) -> PredictionReport:
    return PredictionReport(
        created_at=_now_iso(),
        context=pack["context"],
        target_date=pack["target_date"],
        trigger=trigger,
        engine_version=ENGINE_VERSION,
        market_env={"phase": (pack.get("env") or {}).get("phase"), "last_trade_date": pack["last_trade_date"]},
        predictions=[],
        summary="未发现可预判的候选主题（详见 data_gaps）",
    )


def _summary_line(predictions: list, pack: dict) -> str:
    if not predictions:
        return "未发现可预判的候选主题"
    parts = []
    for p in predictions:
        head = f"{p.theme}→{p.verdict}（{p.score:.2f}/{p.confidence}）"
        if p.echelon:
            head += f"，龙头候选 {p.echelon[0].name}"
        parts.append(head)
    env = (pack.get("env") or {}).get("phase") or "情绪未知"
    return f"目标 {pack['target_date']} | 环境 {env} | " + "；".join(parts)


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
