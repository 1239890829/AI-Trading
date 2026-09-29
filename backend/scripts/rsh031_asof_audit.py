"""Offline audit: freeze 09:55 candidates before joining source outcome labels.

Historical vendor snapshots are not a first-publication/version audit. Results
are exploratory source-pool proxies, never fills or strategy admission evidence.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd


SCORES = {"prior_20d": "ret20", "early_return": "early_ret", "equal_rank": "score_c"}
KEYS = ["trade_date", "thscode"]
FEATURES = KEYS + ["prev_close", "close_21", "history_points", "history_span",
                   "pre_0955_bars", "pre_0955_close", "is_st", "suspended"]


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def unique_keys(frame: pd.DataFrame, label: str) -> None:
    if frame[KEYS].isna().any().any() or frame.duplicated(KEYS).any():
        raise ValueError(f"{label}: null or duplicate identity")


def select_candidates(frame: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Only allow declared inputs; outcome/full-day fields cannot affect ranks."""
    facts = frame[FEATURES].copy()
    unique_keys(facts, "features")
    prices = facts[["prev_close", "close_21", "pre_0955_close"]]
    reasons = {
        "outside_source_exchange_scope": ~facts.thscode.str.fullmatch(r"[0-9]{6}\.(SH|SZ)"),
        "missing_or_invalid_price": ~(np.isfinite(prices).all(axis=1) & prices.gt(0).all(axis=1)),
        "incomplete_prior_sessions": ~(facts.history_points.eq(21) & facts.history_span.eq(20)),
        "missing_asof_bars": ~facts.pre_0955_bars.eq(5),
        "unknown_daily_identity": ~(facts.is_st.isin([0, 1]) & facts.suspended.isin([0, 1])),
        "source_st_or_suspended": facts.is_st.eq(1) | facts.suspended.eq(1),
    }
    excluded = pd.DataFrame(reasons).any(axis=1)
    chosen = facts.loc[~excluded].copy()
    chosen["ret20"] = chosen.prev_close / chosen.close_21 - 1
    chosen["early_ret"] = chosen.pre_0955_close / chosen.prev_close - 1
    chosen["score_c"] = (
        chosen.groupby("trade_date").ret20.rank(method="average", pct=True)
        + chosen.groupby("trade_date").early_ret.rank(method="average", pct=True)
    ) / 2
    return chosen.sort_values(KEYS).reset_index(drop=True), {
        "prior_day_universe": len(facts), "eligible": len(chosen),
        "excluded_unique": int(excluded.sum()),
        "exclusion_reasons_overlapping": {key: int(value.sum()) for key, value in reasons.items()},
    }


def load_pools(path: Path) -> tuple[list[str], pd.DataFrame]:
    seen, events = {}, []
    for line in path.read_text().splitlines():
        row = json.loads(line)
        day, pool, items = row["trade_date"], row["pool"], row["items"]
        if pool not in ("limit-up-pool", "limit-break-pool") or (day, pool) in seen:
            raise ValueError("duplicate or unsupported pool")
        symbols = {item["thscode"] for item in items}
        if len(items) != row["total"] or len(symbols) != len(items):
            raise ValueError("pool count or identity mismatch")
        seen[day, pool] = symbols
        for item in items:
            count = item["continue_day_cnt"] if pool == "limit-up-pool" else 0
            if pool == "limit-up-pool" and (
                isinstance(count, bool) or not isinstance(count, (int, float))
                or not np.isfinite(count) or count < 1 or count != int(count)
            ):
                raise ValueError("invalid source board count")
            events.append({"trade_date": day, "thscode": item["thscode"],
                           "pool_first_board": pool == "limit-up-pool" and count == 1})
    days = sorted({key[0] for key in seen})
    if not days or any((day, pool) not in seen for day in days
                       for pool in ("limit-up-pool", "limit-break-pool")):
        raise ValueError("both complete pools required for every date")
    if any(seen[day, "limit-up-pool"] & seen[day, "limit-break-pool"] for day in days):
        raise ValueError("pool intersection")
    return days, pd.DataFrame(events, columns=KEYS + ["pool_first_board"])


def outcome_labels(minutes: pd.DataFrame, events: pd.DataFrame) -> pd.DataFrame:
    """Unknown source timing stays unknown without changing candidate selection."""
    unique_keys(minutes, "minutes")
    unique_keys(events, "events")
    result = minutes[KEYS + ["first_hit_5m_end", "pool_first_seal"]].merge(
        events, on=KEYS, how="outer", validate="one_to_one", indicator=True)
    cut = pd.to_datetime(result.trade_date + " 09:55")
    hit = pd.to_datetime(result.first_hit_5m_end, errors="coerce")
    seal_text = result.pool_first_seal.astype("string").fillna("")
    # The collector stores HH:MM. Explicit parsing prevents a leading empty
    # (non-pool) row from making pandas infer date-only format for the batch.
    seal = pd.to_datetime(result.trade_date + " " + seal_text,
                          format="%Y-%m-%d %H:%M", errors="coerce")
    first = result.pool_first_board.eq(True)
    unknown = result._merge.eq("right_only") | (
        first & (hit.isna() | hit.dt.normalize().ne(cut.dt.normalize())
                 | (hit.gt(cut) & (seal.isna() | seal.le(cut)))))
    result["label"] = (first & hit.gt(cut)).astype(float).mask(unknown)
    return result[KEYS + ["label"]]


def evaluate(candidates: pd.DataFrame, labels: pd.DataFrame, days: list[str], k: int = 20) -> dict:
    if k <= 0:
        raise ValueError("k must be positive")
    if days != sorted(set(days)) or not set(candidates.trade_date).issubset(days):
        raise ValueError("dates must be unique, ordered and cover all candidates")
    unique_keys(candidates, "candidates")
    unique_keys(labels, "labels")
    if not labels.label.dropna().isin([0, 1]).all():
        raise ValueError("outcome labels must be 0, 1 or unknown")
    joined = candidates.merge(labels, on=KEYS, how="left", validate="one_to_one")
    daily = []
    for day in days:
        group = joined[joined.trade_date.eq(day)]
        unknown, positives = int(group.label.isna().sum()), int(group.label.eq(1).sum())
        item = {"date": day, "eligible": len(group), "known_positives": positives,
                "unknown_outcomes": unknown, "metrics": {}}
        for name, score in SCORES.items():
            ranked = group.sort_values([score, "thscode"], ascending=[False, True], kind="stable")
            top = ranked.head(k)
            hits, missing = int(top.label.eq(1).sum()), int(top.label.isna().sum())
            y = ranked.label.to_numpy()
            ap = float((np.cumsum(y) / np.arange(1, len(y) + 1) * y).sum() / positives) \
                if positives and not unknown else None
            item["metrics"][name] = {"slots": len(top), "hits": hits,
                                     "false_positives": int(top.label.eq(0).sum()),
                                     "unknown_slots": missing, "average_precision": ap}
        daily.append(item)
    metrics = {}
    positives = sum(row["known_positives"] for row in daily)
    unknown = sum(row["unknown_outcomes"] for row in daily)
    for name in SCORES:
        totals = {key: sum(row["metrics"][name][key] for row in daily)
                  for key in ("slots", "hits", "false_positives", "unknown_slots")}
        slots, hits, missing = totals["slots"], totals["hits"], totals["unknown_slots"]
        totals["precision_bounds"] = [hits / slots, (hits + missing) / slots] if slots else None
        totals["recall"] = hits / positives if positives and not unknown else None
        totals["weighted_average_precision"] = sum(
            (row["metrics"][name]["average_precision"] or 0) * row["known_positives"]
            for row in daily) / positives if positives and not unknown else None
        metrics[name] = totals
    return {"days": daily, "known_positives": positives, "unknown_outcomes": unknown,
            "metrics": metrics}


def load_inputs(database: Path, minute_paths: list[Path], status_path: Path,
                status_format: str, days: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    status = pd.read_csv(status_path)
    if status_format == "bigquant":
        status = status.rename(columns={"date": "trade_date", "instrument": "thscode",
                                        "st_status": "is_st"})
    else:
        status["suspended"] = 1 - status.trade_status
    status = status[KEYS + ["is_st", "suspended"]]
    unique_keys(status, "status")
    with duckdb.connect(str(database), read_only=True) as con:
        con.execute("SET threads=2")
        minutes = con.read_parquet([str(p) for p in minute_paths]).df()
        minutes = minutes[minutes.trade_date.isin(days)].copy()
        unique_keys(minutes, "minutes")
        history = con.execute("""
          WITH bars AS (
            SELECT thscode, CAST(to_timestamp(date_ms/1000.0) AT TIME ZONE 'Asia/Shanghai'
                                 AS DATE) AS day, close_price FROM daily_k
          ), calendar AS (
            SELECT day, row_number() OVER(ORDER BY day) session_no,
                   lead(day) OVER(ORDER BY day) next_day FROM (SELECT DISTINCT day FROM bars)
          ), history AS (
            SELECT thscode, c.next_day, close_price AS prev_close,
                   lag(close_price,20) OVER w AS close_21,
                   c.session_no-lag(c.session_no,20) OVER w AS history_span,
                   count(CASE WHEN isfinite(close_price) AND close_price>0 THEN 1 END)
                     OVER(PARTITION BY thscode ORDER BY b.day ROWS BETWEEN 20 PRECEDING AND CURRENT ROW)
                     AS history_points
            FROM bars b JOIN calendar c USING(day)
            WHERE b.day >= CAST(? AS DATE)-INTERVAL 90 DAY AND b.day < CAST(? AS DATE)
            WINDOW w AS (PARTITION BY thscode ORDER BY b.day)
          )
            SELECT CAST(next_day AS VARCHAR) trade_date, * EXCLUDE(next_day)
          FROM history WHERE CAST(next_day AS VARCHAR) IN (SELECT unnest(?))
            AND regexp_full_match(thscode, '[0-9]{6}\\.(SH|SZ)')
        """, [days[0], days[-1], days]).df()
    unique_keys(history, "prior history")
    facts = history.merge(minutes[KEYS + ["pre_0955_bars", "pre_0955_close"]],
                          on=KEYS, how="left", validate="one_to_one")
    return facts.merge(status, on=KEYS, how="left", validate="one_to_one"), minutes


def run(database: Path, minute_paths: list[Path], status: Path, status_format: str,
        pools: Path, protocol: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("refusing to overwrite an existing research output")
    inputs = [*minute_paths, status, pools, protocol]
    before = [digest(path) for path in inputs]
    days, events = load_pools(pools)
    facts, minutes = load_inputs(database, minute_paths, status, status_format, days)
    candidates, coverage = select_candidates(facts)
    labels = outcome_labels(minutes, events)
    result = evaluate(candidates, labels, days)
    if before != [digest(path) for path in inputs]:
        raise ValueError("input snapshot changed during audit")
    result.update({"kind": "exploratory_outcome_independent_candidate_audit",
                   "exchange_scope": ["SH", "SZ"],
                   "coverage": coverage, "protocol_sha256": digest(protocol),
                   "script_sha256": digest(Path(__file__)),
                   "limits": ["daily vendor identity has no first-publication/revision proof",
                              "source-pool first-board timing proxy, not exchange event completeness",
                              "already observed windows; not new holdout or fill evidence"],
                   "inputs": [{"file": p.name, "sha256": sha}
                              for p, sha in zip(inputs, before)]})
    output.mkdir(parents=True)
    # Freeze the consumed DB-derived facts, including excluded rows. Later DB
    # refreshes must not prevent reproducing selection and missing-input counts.
    facts[FEATURES].to_parquet(output / "input-facts.parquet", index=False)
    candidates.to_parquet(output / "candidates.parquet", index=False)
    labels.to_parquet(output / "source-labels.parquet", index=False)
    result["candidates_sha256"] = digest(output / "candidates.parquet")
    result["labels_sha256"] = digest(output / "source-labels.parquet")
    result["facts_sha256"] = digest(output / "input-facts.parquet")
    (output / "report.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--database", type=Path, required=True)
    p.add_argument("--minutes", type=Path, nargs="+", required=True)
    p.add_argument("--status", type=Path, required=True)
    p.add_argument("--status-format", choices=["baostock", "bigquant"], required=True)
    p.add_argument("--pools", type=Path, required=True)
    p.add_argument("--protocol", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    args = p.parse_args()
    report = run(args.database, args.minutes, args.status, args.status_format,
                 args.pools, args.protocol, args.output)
    print(json.dumps({key: report[key] for key in ("kind", "coverage", "metrics", "limits")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
