"""影子参数的**离线对照评估**（S2-11）。

**背景（为什么需要这一层）**：参数白名单从 1 扩到 5 之后，影子评估器却只有一个
（`picks_style_offsets_json`），其余四个一律返回 `shadow_eval_unsupported`
——影子队列里躺着一堆"没人验"的变更，promote 与否全凭人工拍板，
"[参数]变更存活率"这类指标也就失去了对照依据。

**本模块做什么**：用**已落库的历史快照**做对照模拟——
`DailyPickSet.items`（入选，含 score）/ `.rejected`（深评落选者，含 score）/
`.replaced`（换股记录，含 delta），配合 `DailyPickReview`（事后 verdict/excess）
验证被影响到的票**事后到底怎么样**。

不重跑选股、不碰网络、不调 LLM，只是把历史候选池在新参数下重新过一遍门槛。

## 口径边界（必须与结论一起声明）

1. **这是反事实的静态重放**：假设"评分分布不变、只改门槛"。真实情况里改了门槛
   会连带改变候选池构成与评分分布（例如门槛抬高后，深评池会换一批人），
   因此结论是**方向性的**，不是精确的收益预测。
2. **样本来自已落库的历史日**（当前约 10 个交易日）。不足 `MIN_SAMPLE_DAYS`
   一律 `insufficient`，**不给结论**——样本少 ≠ 无效，但也撑不起方向判断。
3. **日期对齐**：`review` 与 `set` 按**同一 `date`** 匹配（复盘记录的是该组合日
   对应标的的表现）。若某日无复盘记录，该日只计影响面、不计事后验证。
4. 本模块**不决定是否 promote**，只给 `supports` / `opposes` / `neutral` 与证据；
   终审仍是人（方案 §方向 5：全自动写回被明确排除）。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Iterable

log = logging.getLogger(__name__)

#: marketdb 路径（与 `app/factors/evaluate.py` 等同一份库）
DEFAULT_DB_PATH = Path(__file__).resolve().parents[2] / "data" / "marketdb" / "market.duckdb"

#: 少于此天数不给方向结论（只有影响面）
MIN_SAMPLE_DAYS = 3

#: 事后验证所需的最小 review 样本数（低于此值不比较 good 比例）
MIN_REVIEW_SAMPLES = 5

#: 组合容量上限（用于模拟"补位"）。真实值从 picks 模块读，读不到用 5。
DEFAULT_CAPACITY = 5

VERDICT_SUPPORTS = "supports"        # 证据支持这次变更
VERDICT_OPPOSES = "opposes"          # 证据反对这次变更
VERDICT_NEUTRAL = "neutral"          # 无明显差异 / 无影响面
VERDICT_INSUFFICIENT = "insufficient"  # 样本不足，不给结论
VERDICT_NOT_APPLICABLE = "not_applicable"  # 该参数本就不需要离线评估


# ---------------------------------------------------------------- 历史读取


def load_history(session_factory, limit: int = 60) -> tuple[list[dict], dict, str | None]:
    """读历史组合快照与事后复盘。

    :returns: `(sets, reviews, error)`。`reviews` 为 `{(date, symbol): {verdict, excess_pct}}`。

    ⚠️ **读失败必须回传 error，不能伪装成"没有历史数据"**：
    「库挂了」与「历史上确实没有组合」是两回事，前者要修、后者只需等数据积累，
    混为一谈会让人以为系统里本来就没数据（三态纪律）。
    """
    try:
        from sqlalchemy import select

        from app.models.daily_pick import DailyPickReview, DailyPickSet
    except Exception as exc:  # noqa: BLE001
        log.warning("影子评估：模型导入失败 %s", exc)
        return [], {}, f"模型导入失败：{exc}"

    sets: list[dict] = []
    reviews: dict[tuple[str, str], dict] = {}
    try:
        with session_factory() as db:
            rows = db.execute(
                select(DailyPickSet).order_by(DailyPickSet.date.desc()).limit(limit)
            ).scalars().all()
            for r in rows:
                sets.append({
                    "date": r.date,
                    "items": _json_list(r.items),
                    "rejected": _json_list(r.rejected),
                    "replaced": _json_list(r.replaced),
                })
            rvs = db.execute(select(DailyPickReview)).scalars().all()
            for rv in rvs:
                reviews[(rv.date, rv.symbol)] = {
                    "verdict": rv.verdict,
                    "excess_pct": rv.excess_pct,
                }
    except Exception as exc:  # noqa: BLE001
        log.warning("影子评估：历史读取失败 %s", exc)
        return [], {}, f"历史读取失败：{exc}"
    return sets, reviews, None


def _json_list(raw: str | None) -> list[dict]:
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [d for d in data if isinstance(d, dict)]


def _capacity() -> int:
    """组合容量上限（补位时最多填到几只）。取自 `picks.engine.MAX_PICKS`——
    **不是** `picks_max_swaps_per_day`（那是换股次数，两者语义完全不同）。"""
    try:
        from app.picks.engine import MAX_PICKS

        return max(int(MAX_PICKS), 1)
    except Exception:  # noqa: BLE001
        return DEFAULT_CAPACITY


# ---------------------------------------------------------------- 纯函数模拟


def simulate_min_score(
    sets: list[dict],
    threshold: float,
    capacity: int = DEFAULT_CAPACITY,
    *,
    base_threshold: float | None = None,
) -> dict:
    """在给定入选门槛下**重放**历史组合构成（纯函数，便于单测）。

    模拟规则（与线上一致的两条）：
      - 组合内成员 `score < threshold` → **出列**（跌破即出列）
      - 空出的名额由深评落选者补位

    ⚠️ **补位只补「因门槛降低才刚够格」的人**（`base <= score < threshold` 不成立时取
    `base_threshold <= score < threshold`）：候选取 `base_threshold <= s < threshold` 不成立，
    实际取 **`threshold <= s < base_threshold`**（门槛**降低**时新释放的一批）。

    这个边界是刻意卡死的：若放宽成"任何 `score >= threshold` 的落选者都能补"，
    那么**门槛不变**（`threshold == base_threshold`）时也会凭空补人——
    历史上组合没填满容量，往往是被板块去重/深评上限等其它约束卡住，
    而不是"他们不够门槛"。用宽松条件会把这些约束**当成不存在**，
    产出一个历史上从未发生过的组合（2026-09-11 单测即抓到此例）。

    :param base_threshold: 原门槛；**强烈建议传**。不传时退化为"不补位"。
    :returns: `{"days", "dropped", "added", "kept", "size_before", "size_after"}`
    """
    days: list[dict] = []
    dropped: list[dict] = []
    added: list[dict] = []
    kept: list[dict] = []

    for s in sets:
        items = [i for i in s.get("items") or [] if _num(i.get("score")) is not None]
        rejected = [r for r in s.get("rejected") or [] if _num(r.get("score")) is not None]
        keep = [i for i in items if (_num(i.get("score")) or 0) >= threshold]
        drop = [i for i in items if (_num(i.get("score")) or 0) < threshold]

        slots = max(capacity - len(keep), 0)
        if base_threshold is None or threshold >= base_threshold:
            pool: list[dict] = []  # 未给原门槛、或门槛未降低 ⇒ 不补位
        else:
            # 只补「旧门槛够不着、新门槛够得着」的这批
            pool = sorted(
                (r for r in rejected
                 if threshold <= (_num(r.get("score")) or 0) < base_threshold),
                key=lambda r: _num(r.get("score")) or 0,
                reverse=True,
            )
        add = pool[:slots]

        days.append({
            "date": s.get("date"),
            "size_before": len(items),
            "size_after": len(keep) + len(add),
            "dropped": [i.get("symbol") for i in drop],
            "added": [i.get("symbol") for i in add],
        })
        for i in drop:
            dropped.append({"date": s.get("date"), **i})
        for i in add:
            added.append({"date": s.get("date"), **i})
        for i in keep:
            kept.append({"date": s.get("date"), **i})

    return {
        "days": days,
        "dropped": dropped,
        "added": added,
        "kept": kept,
        "size_before": sum(d["size_before"] for d in days),
        "size_after": sum(d["size_after"] for d in days),
    }


def simulate_replace_threshold(sets: list[dict], threshold: float) -> dict:
    """在给定换股分差门槛下重放历史换股（纯函数）。

    `replaced[i].delta` 是历史上**实际发生**的换股分差；门槛抬到 T 之后，
    `delta < T` 的那些换股将不再发生。
    """
    blocked: list[dict] = []
    allowed: list[dict] = []
    without_delta = 0
    for s in sets:
        for rep in s.get("replaced") or []:
            delta = _num(rep.get("delta"))
            if delta is None:
                without_delta += 1
                continue
            rec = {"date": s.get("date"), **rep}
            (allowed if delta >= threshold else blocked).append(rec)
    return {
        "blocked": blocked,
        "allowed": allowed,
        "total": len(blocked) + len(allowed),
        "without_delta": without_delta,
    }


def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------- 全市场走势补验

#: 补验用的默认前向窗口（交易日），与 `DailyPickReview` 的 T+5 口径保持一致
FORWARD_HORIZON = 5


def load_market_forward_gains(
    pairs: list[tuple[str, str]],
    horizon: int = FORWARD_HORIZON,
    db_path: Path | None = None,
) -> dict[tuple[str, str], float]:
    """查 marketdb，给 (symbol, 交易日) 补一段**市场中性**的前向收益（%）。

    **为什么需要它**：放宽 `picks_min_pick_score` 时补进来的是**历史落选者**——
    他们从未入选 ⇒ `DailyPickReview` 里根本没有他们 ⇒ 事后验证数恒为 0。
    没有这个函数，"放宽门槛"这个方向就永远无法评估（只能给影响面、给不了方向）。

    ⚠️ marketdb 是**本项目自己的库**，不是外部数据源——此前把它误判成"新增数据源依赖"
    而搁置了这项，属过度保守。

    ⚠️ **适用边界（实测确认，别误读成缺陷）**：需要"该日之后 h 个交易日"的行情，
    所以**距今不足 h 个交易日的日子算不出来**（那是未来）。实测：库内最新到 2026-09-11，
    查 2026-08-03 正常返回，查 2026-09-10 返回空。因此本补验对**近期**的落选者无效、
    对**较早**的有效——`DailyPickSet` 数据越积累，能补验的样本越多。
    查不到时返回空（由调用方降级），**不臆造 0**。

    :param pairs: `[(symbol, 交易日YYYY-MM-DD)]`
    :returns: `{(symbol, 交易日): 超额收益%}`，查不到的键不出现（**不臆造 0**）。
    """
    if not pairs:
        return {}
    try:
        import duckdb

        from app.data_providers.ths import to_thscode
    except Exception as exc:  # noqa: BLE001
        log.warning("marketdb 补验不可用：%s", exc)
        return {}

    codes = sorted({to_thscode(s) for s, _ in pairs})
    dates = sorted({d for _, d in pairs})
    day_ms = [_date_ms(d) for d in dates]
    lo, hi = min(day_ms), max(day_ms)
    # 缓冲：LEAD 要往后看 h 个交易日，给足自然日余量（含周末/假期）
    buf_ms = (horizon + 4) * 86_400_000

    path = str(db_path or DEFAULT_DB_PATH)
    try:
        con = duckdb.connect(path, read_only=True)
    except Exception as exc:  # noqa: BLE001
        log.warning("marketdb 连接失败（%s）：%s", path, exc)
        return {}

    try:
        # 个股：在限定窗口内用 LEAD 取第 h 个交易日后的收盘价
        codes_sql = ", ".join("'" + c.replace("'", "") + "'" for c in codes)
        dates_sql = ", ".join(str(v) for v in day_ms)
        rows = con.execute(
            f"""
            WITH w AS (
                SELECT thscode, date_ms, close_price,
                       LEAD(close_price, {int(horizon)}) OVER
                         (PARTITION BY thscode ORDER BY date_ms) AS c_h
                FROM daily_k
                WHERE thscode IN ({codes_sql})
                  AND date_ms BETWEEN {lo - buf_ms} AND {hi + buf_ms}
            )
            SELECT thscode, date_ms, (c_h / close_price - 1.0) * 100.0
            FROM w
            WHERE c_h IS NOT NULL AND close_price > 0 AND date_ms IN ({dates_sql})
            """
        ).fetchall()
        # 市场同期基准：同一批日期的全市场均值（中性化的分母）
        mkt = {
            int(r[0]): float(r[1])
            for r in con.execute(
                f"""
                WITH w AS (
                    SELECT thscode, date_ms, close_price,
                           LEAD(close_price, {int(horizon)}) OVER
                             (PARTITION BY thscode ORDER BY date_ms) AS c_h
                    FROM daily_k
                    WHERE date_ms BETWEEN {lo - buf_ms} AND {hi + buf_ms}
                )
                SELECT date_ms, avg((c_h / close_price - 1.0) * 100.0)
                FROM w
                WHERE c_h IS NOT NULL AND close_price > 0 AND date_ms IN ({dates_sql})
                GROUP BY date_ms
                """
            ).fetchall()
        }
    except Exception as exc:  # noqa: BLE001
        log.warning("marketdb 前向收益查询失败：%s", exc)
        return {}
    finally:
        con.close()

    out: dict[tuple[str, str], float] = {}
    by_ms = {ms: d for d, ms in zip(dates, day_ms)}
    for thscode, ms, ret in rows:
        base = mkt.get(int(ms))
        if base is None:
            continue  # 没有市场基准就不给超额——宁缺勿滥
        day = by_ms.get(int(ms))
        if day is None:
            continue
        out[(thscode.split(".")[0], day)] = round(float(ret) - base, 3)
    return out


def _date_ms(day: str) -> int:
    """交易日字符串 → marketdb 的 `date_ms`（**上海零点**毫秒）。

    ⚠️ 必须按北京时间取零点：用本地时区会在跨零点时差一天（既有教训）。
    """
    from datetime import datetime as _dt

    from app.core.bjtime import BJ_TZ

    return int(_dt.strptime(day, "%Y-%m-%d").replace(tzinfo=BJ_TZ).timestamp() * 1000)


# ---------------------------------------------------------------- 事后验证


def _ratio_from_market(
    records: list[dict], gains: dict[tuple[str, str], float]
) -> dict:
    """用 marketdb 的前向**超额**收益给一批记录算 good 比例（>0 视为跑赢市场）。

    查不到的**直接剔除**，绝不按 0 计入——0 表示"恰好持平"，会系统性稀释结论。
    """
    vals = [gains.get((r.get("symbol"), r.get("date"))) for r in records]
    vals = [v for v in vals if v is not None and v == v]  # 去 None 与 NaN
    if not vals:
        return {"n": 0, "good": 0, "ratio": None}
    return {"n": len(vals), "good": sum(1 for v in vals if v > 0),
            "ratio": sum(1 for v in vals if v > 0) / len(vals)}


def _good_ratio(records: Iterable[dict], reviews: dict) -> dict:
    """一批记录里已复盘者的 good 比例（`{"n", "good", "ratio"}`）。"""
    vals = [reviews.get((r.get("date"), r.get("symbol"))) for r in records]
    vals = [v for v in vals if v]
    if not vals:
        return {"n": 0, "good": 0, "ratio": None}
    good = sum(1 for v in vals if v.get("verdict") == "good")
    return {"n": len(vals), "good": good, "ratio": good / len(vals)}


def _compare(dropped: list[dict], kept: list[dict], reviews: dict) -> dict:
    """被剔除者 vs 被保留者的事后对比。样本不足时 `ratio` 为 None。"""
    d = _good_ratio(dropped, reviews)
    k = _good_ratio(kept, reviews)
    if d["n"] < MIN_REVIEW_SAMPLES or k["n"] < MIN_REVIEW_SAMPLES:
        return {"dropped": d, "kept": k, "comparable": False}
    return {"dropped": d, "kept": k, "comparable": True}


# ---------------------------------------------------------------- 各参数评估器


def _base(key: str, before: Any, after: Any) -> dict:
    return {
        "key": key, "before": before, "after": after,
        "verdict": VERDICT_NEUTRAL, "note": "", "metrics": {}, "sample_days": 0,
    }


def eval_min_pick_score(
    *,
    before: Any,
    after: Any,
    sets: list[dict],
    reviews: dict,
    market_gains: dict[tuple[str, str], float] | None = None,
) -> dict:
    """入选门槛变更：抬高 → 剔除低分者；降低 → 落选者补位。

    判据（**只在可比时给方向**）：
      - 抬高门槛且被剔除者 good 比例**低于**保留者 → `supports`（剔除的是弱的）
      - 抬高门槛但被剔除者 good 比例**不低于**保留者 → `opposes`（会误杀）
    """
    out = _base("picks_min_pick_score", before, after)
    t_new, t_old = _num(after), _num(before)
    if t_new is None or t_old is None:
        out["verdict"] = VERDICT_INSUFFICIENT
        out["note"] = "前后值无法解析为数值"
        return out
    if len(sets) < MIN_SAMPLE_DAYS:
        out["verdict"] = VERDICT_INSUFFICIENT
        out["note"] = f"历史组合仅 {len(sets)} 日（需 ≥{MIN_SAMPLE_DAYS}）"
        return out

    sim = simulate_min_score(sets, t_new, _capacity(), base_threshold=t_old)
    days = max(len(sets), 1)
    out["sample_days"] = len(sets)
    out["metrics"] = {
        "threshold_before": t_old, "threshold_after": t_new,
        # 总量与日均**都给**：只给总量会被误读成"一次组合有 46 只"，
        # 实际是 N 日累计（每日 4~5 只）。判读组合规模请一律看日均。
        "size_before_total": sim["size_before"], "size_after_total": sim["size_after"],
        "avg_size_before": round(sim["size_before"] / days, 2),
        "avg_size_after": round(sim["size_after"] / days, 2),
        "dropped": len(sim["dropped"]), "added": len(sim["added"]),
    }

    if not sim["dropped"] and not sim["added"]:
        out["verdict"] = VERDICT_NEUTRAL
        out["note"] = (f"门槛 {t_old} → {t_new} 在 {len(sets)} 日历史里不改变任何组合构成"
                       f"（成员分全在区间外）")
        return out

    raising = t_new > t_old
    # ⚠️ 可比性必须按**方向**取不同的对照组：
    #   抬高门槛 → 看「被剔除者 vs 保留者」；
    #   放宽门槛 → 看「补入者 vs 保留者」（此时 dropped 恒为空，若一律用 dropped
    #   判可比性，放宽方向会被永久屏蔽成 neutral —— 2026-09-11 单测抓到此例）。
    dropped_stat = _good_ratio(sim["dropped"], reviews)
    added_stat = _good_ratio(sim["added"], reviews)
    kept_stat = _good_ratio(sim["kept"], reviews)

    # **结构性盲区补验**：补入者是历史落选者，复盘表里没有他们 ⇒ `added_stat` 恒为 0 条。
    # 此时改用 marketdb 的全市场走势补一段前向超额收益，让"放宽到底好不好"能被判定。
    #
    # ⚠️ **必须两边同口径**：只补 added 不补 kept 的话，`kept_stat` 仍不足样本下限，
    # 判定依旧落到"可比性不足" ⇒ 还是 neutral（2026-09-11 首版就踩了这个坑）。
    added_source = "review"
    if market_gains:
        if not raising and sim["added"] and added_stat["n"] < MIN_REVIEW_SAMPLES:
            added_stat = _ratio_from_market(sim["added"], market_gains)
            if added_stat["n"]:
                added_source = "marketdb"
        if kept_stat["n"] < MIN_REVIEW_SAMPLES:
            kept_stat = _ratio_from_market(sim["kept"], market_gains) or kept_stat
    out["metrics"]["review"] = {"dropped": dropped_stat, "added": added_stat,
                                "kept": kept_stat, "added_source": added_source}

    changed_stat = dropped_stat if raising else added_stat
    label = "剔除" if raising else "补入"
    if kept_stat["n"] < MIN_REVIEW_SAMPLES or changed_stat["n"] < MIN_REVIEW_SAMPLES:
        out["verdict"] = VERDICT_NEUTRAL
        # 放宽方向有个**结构性**盲区：补入者来自历史落选池，从未进过组合
        # ⇒ 复盘表里根本没有他们 ⇒ 复盘数恒为 0。必须说清这是数据边界，
        # 否则会被读成"这次补位没影响"。
        why = ""
        if not raising and sim["added"] and added_stat["n"] == 0:
            why = ("（补入者均来自历史落选池，从未入选 ⇒ 无复盘记录；"
                   "可传 `market_gains` 用全市场走势补验，本次未提供或查不到对应行情）")
        out["note"] = (f"影响 {len(sim['dropped'])} 剔除 / {len(sim['added'])} 补位，"
                       f"但可比对的事后复盘不足 {MIN_REVIEW_SAMPLES} 条"
                       f"（{label} {changed_stat['n']}、保留 {kept_stat['n']}）"
                       f" → 只给影响面，不给方向{why}")
        return out

    c_ratio, k_ratio = changed_stat["ratio"], kept_stat["ratio"]
    if raising:
        # 被剔除者比保留者弱 ⇒ 剔除得对
        out["verdict"] = VERDICT_SUPPORTS if c_ratio < k_ratio else VERDICT_OPPOSES
    else:
        # 补入者不弱于保留者 ⇒ 放宽得对
        out["verdict"] = VERDICT_SUPPORTS if c_ratio >= k_ratio else VERDICT_OPPOSES

    out["note"] = (f"{label} {changed_stat['n']} 只（good {c_ratio:.0%}）"
                   f" vs 保留 {kept_stat['n']} 只（good {k_ratio:.0%}）"
                   + (f"；另有 {len(sim['added'])} 只补入" if raising and sim["added"] else ""))
    return out


def eval_replace_threshold(*, before: Any, after: Any, sets: list[dict], reviews: dict) -> dict:
    """换股分差门槛：抬高 → 部分历史换股将不再发生。

    ⚠️ 该参数的文档已注明「分差型门槛在分层候选池下几乎不起作用」，
    本评估器给出的是**实测证据**——若抬高后 blocked=0，那就是"确实不起作用"的实据。
    """
    out = _base("picks_replace_threshold", before, after)
    t_new = _num(after)
    if t_new is None:
        out["verdict"] = VERDICT_INSUFFICIENT
        out["note"] = "新值无法解析为数值"
        return out
    if len(sets) < MIN_SAMPLE_DAYS:
        out["verdict"] = VERDICT_INSUFFICIENT
        out["note"] = f"历史组合仅 {len(sets)} 日（需 ≥{MIN_SAMPLE_DAYS}）"
        return out

    sim = simulate_replace_threshold(sets, t_new)
    out["sample_days"] = len(sets)
    out["metrics"] = {
        "threshold_after": t_new,
        "total_swaps": sim["total"],
        "blocked": len(sim["blocked"]),
        "allowed": len(sim["allowed"]),
        "without_delta": sim["without_delta"],
    }

    if sim["total"] == 0:
        out["verdict"] = VERDICT_NEUTRAL
        out["note"] = f"{len(sets)} 日历史里没有换股记录，无从评估"
        return out
    if not sim["blocked"]:
        out["verdict"] = VERDICT_NEUTRAL
        out["note"] = (f"门槛抬到 {t_new} 不会拦下任何历史换股（共 {sim['total']} 次，"
                       f"最小分差均 ≥ 该值）⇒ 该门槛在此区间内不起作用")
        return out

    # 被拦下的换股：换入方 vs 换出方事后谁更好
    wins_in = wins_out = 0
    for rep in sim["blocked"]:
        din = reviews.get((rep.get("date"), rep.get("in")))
        dout = reviews.get((rep.get("date"), rep.get("out")))
        if not din or not dout:
            continue
        if (din.get("excess_pct") or 0) > (dout.get("excess_pct") or 0):
            wins_in += 1
        else:
            wins_out += 1
    out["metrics"]["blocked_review"] = {"n": wins_in + wins_out, "in_better": wins_in,
                                        "out_better": wins_out}
    if wins_in + wins_out < MIN_REVIEW_SAMPLES:
        out["verdict"] = VERDICT_NEUTRAL
        out["note"] = (f"将拦下 {len(sim['blocked'])}/{sim['total']} 次换股，"
                       f"但可比对复盘不足 {MIN_REVIEW_SAMPLES} 条 → 只给影响面")
        return out
    out["verdict"] = VERDICT_OPPOSES if wins_in > wins_out else VERDICT_SUPPORTS
    out["note"] = (f"将拦下 {len(sim['blocked'])}/{sim['total']} 次换股；"
                   f"其中换入方事后更优 {wins_in}、换出方更优 {wins_out}"
                   f" ⇒ {'拦错了' if wins_in > wins_out else '拦得对'}")
    return out


def eval_max_swaps_per_day(*, before: Any, after: Any, sets: list[dict], reviews: dict) -> dict:
    """每日换股上限：调低 → 超出上限的换股被截断。

    只给**影响面**：截断会同时影响换入与换出，事后归因需要真实的组合级回放
    （本模块不做），因此不给 supports/opposes。
    """
    out = _base("picks_max_swaps_per_day", before, after)
    lim = _num(after)
    if lim is None:
        out["verdict"] = VERDICT_INSUFFICIENT
        out["note"] = "新值无法解析为数值"
        return out
    if len(sets) < MIN_SAMPLE_DAYS:
        out["verdict"] = VERDICT_INSUFFICIENT
        out["note"] = f"历史组合仅 {len(sets)} 日（需 ≥{MIN_SAMPLE_DAYS}）"
        return out

    affected_days = 0
    truncated = 0
    total = 0
    for s in sets:
        reps = s.get("replaced") or []
        total += len(reps)
        if len(reps) > lim:
            affected_days += 1
            truncated += len(reps) - lim
    out["sample_days"] = len(sets)
    out["metrics"] = {"limit_after": lim, "total_swaps": total,
                      "affected_days": affected_days, "truncated_swaps": truncated}
    if truncated:
        out["verdict"] = VERDICT_NEUTRAL
        out["note"] = (f"上限 {lim} 会截断 {truncated} 次换股（影响 {affected_days}/{len(sets)} 日）；"
                       f"组合级后果需真实回放，此处只给影响面")
    else:
        out["verdict"] = VERDICT_NEUTRAL
        out["note"] = f"上限 {lim} 不影响历史换股（单日最多未超该值）"
    return out


def eval_intraday_top_limit(*, before: Any, after: Any, sets: list[dict], reviews: dict) -> dict:
    """盘中跟踪条数上限：L0 纯展示容量，不改筛选逻辑 ⇒ **无需**离线评估。

    显式返回 `not_applicable` 而不是 `unsupported`：前者是"评估过、结论是不需要评估"，
    后者会让人以为缺能力。
    """
    out = _base("picks_intraday_top_limit", before, after)
    out["verdict"] = VERDICT_NOT_APPLICABLE
    out["note"] = ("纯展示容量参数（风险 L0），不改变任何筛选或评分逻辑，"
                   "离线对照评估对其无意义——直接人工拍板即可")
    out["metrics"] = {"after": after}
    return out


EVALUATORS = {
    "picks_min_pick_score": eval_min_pick_score,
    "picks_replace_threshold": eval_replace_threshold,
    "picks_max_swaps_per_day": eval_max_swaps_per_day,
    "picks_intraday_top_limit": eval_intraday_top_limit,
}


def evaluate_shadow(key: str, *, before: Any, after: Any, session_factory=None) -> dict:
    """统一入口：给一个白名单参数的影子变更做离线对照评估。

    返回 `{"key", "before", "after", "verdict", "note", "metrics", "sample_days", "caveat"}`；
    未知 key → `{"verdict": "unsupported"}`（保持与既有行为一致）。
    """
    fn = EVALUATORS.get(key)
    if fn is None:
        return {"key": key, "before": before, "after": after,
                "verdict": "unsupported", "note": "该参数暂无离线对照评估器，需人工拍板"}

    if session_factory is None:
        try:
            from app.core.db import get_session_factory

            session_factory = get_session_factory()
        except Exception as exc:  # noqa: BLE001
            return {"key": key, "verdict": VERDICT_INSUFFICIENT,
                    "note": f"无法获取 session_factory：{exc}"}

    sets, reviews, err = load_history(session_factory)
    if err:
        # 读不到历史 ≠ 样本不足：前者是故障，后者只是数据还没攒够
        return {"key": key, "before": before, "after": after,
                "verdict": VERDICT_INSUFFICIENT, "sample_days": 0,
                "note": f"{err} ⇒ 无法评估（并非样本不足）"}
    # 放宽门槛时需要全市场走势补验（补入者是落选者，复盘表里没有他们）
    market_gains: dict[tuple[str, str], float] = {}
    if key == "picks_min_pick_score":
        try:
            t_new, t_old = float(after), float(before)
        except (TypeError, ValueError):
            t_new = t_old = None
        if t_new is not None and t_old is not None and t_new < t_old:
            sim = simulate_min_score(sets, t_new, _capacity(), base_threshold=t_old)
            # added 与 kept **都要查**——判定是两者的对比，只补一边等于没补
            pairs = [(r.get("symbol"), r.get("date"))
                     for r in (sim["added"] + sim["kept"])
                     if r.get("symbol") and r.get("date")]
            if pairs:
                market_gains = load_market_forward_gains(pairs)

    try:
        out = fn(before=before, after=after, sets=sets, reviews=reviews,
                 market_gains=market_gains or None)
    except Exception as exc:  # noqa: BLE001 —— 评估器炸了不能拖垮 promote 流程
        log.warning("影子评估失败 key=%s: %s", key, exc)
        return {"key": key, "before": before, "after": after,
                "verdict": VERDICT_INSUFFICIENT, "note": f"评估失败：{exc}"}
    if market_gains:
        out["market_verify"] = {"pairs_queried": len(market_gains),
                                "horizon": FORWARD_HORIZON}

    out["caveat"] = ("静态重放：假设评分分布不变、只改门槛，真实改动会连带改变候选池；"
                     "结论是方向性的，不是收益预测")
    return out
