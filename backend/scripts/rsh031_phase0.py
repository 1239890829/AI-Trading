"""RSH-031 Phase 0: read-only historical daily-bar proxy and denominator audit.

This deliberately does not certify official limit-up events or point-in-time
theme/news evidence. It never reads the validation or untouched holdout rows.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb


PROTOCOL = "rsh031-phase0-v1"
EXPLORATION_END = date(2023, 12, 31)
VALIDATION_START = date(2024, 1, 1)
HOLDOUT_START = date(2025, 1, 1)
MIN_HISTORY_BARS = 20
BEIJING = ZoneInfo("Asia/Shanghai")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _quote(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def build(db_path: Path, output_dir: Path) -> dict:
    """Write one immutable-versioned exploration asset set from local marketdb."""
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(f"refuse to overwrite existing research assets: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    exploration_stop_ms = int(datetime.combine(VALIDATION_START, time.min,
                                                tzinfo=BEIJING).timestamp() * 1000)
    con = duckdb.connect(":memory:")
    try:
        con.execute(f"ATTACH {_quote(db_path)} AS source (READ_ONLY)")
        columns = {row[0] for row in con.execute("DESCRIBE source.daily_k").fetchall()}
        expected = {"thscode", "date_ms", "open_price", "high_price", "low_price",
                    "close_price", "volume", "turnover"}
        if not expected <= columns:
            raise ValueError(f"daily_k missing columns: {sorted(expected - columns)}")
        factor_columns = {row[0] for row in con.execute("DESCRIBE source.adjust_factor").fetchall()}
        if not {"thscode", "ex_date_ms"} <= factor_columns:
            raise ValueError("adjust_factor missing identity/date columns")
        # Source-wide count/date bounds and file digest are provenance metadata;
        # no validation/holdout price or outcome is queried or summarized.
        source_bounds = con.execute(
            "SELECT count(*), min(date_ms), max(date_ms) FROM source.daily_k"
        ).fetchone()
        con.execute(f"""
            CREATE TEMP TABLE observations AS
            WITH bars AS (
                SELECT thscode, date_ms,
                    CAST(to_timestamp(date_ms / 1000.0) AT TIME ZONE 'Asia/Shanghai'
                         AS DATE) AS trade_date,
                    open_price, high_price, low_price, close_price, volume, turnover,
                    LAG(close_price) OVER w AS prev_close,
                    LAG(turnover) OVER w AS prev_turnover,
                    ROW_NUMBER() OVER w AS history_bars,
                    LAG(date_ms) OVER w AS prev_date_ms
                FROM source.daily_k
                WHERE date_ms < {exploration_stop_ms}
                WINDOW w AS (PARTITION BY thscode ORDER BY date_ms)
            ), actions AS (
                SELECT DISTINCT thscode, ex_date_ms FROM source.adjust_factor
                WHERE ex_date_ms < {exploration_stop_ms}
            ), joined AS (
                SELECT bars.*, actions.ex_date_ms IS NOT NULL AS corporate_action_on_date
                FROM bars LEFT JOIN actions
                  ON bars.thscode = actions.thscode AND bars.date_ms = actions.ex_date_ms
            ), regimes AS (
                SELECT *,
                    CASE
                        WHEN thscode LIKE '300%.SZ' OR thscode LIKE '301%.SZ'
                          OR thscode LIKE '302%.SZ' THEN 'chinext'
                        WHEN thscode LIKE '688%.SH' OR thscode LIKE '689%.SH' THEN 'star'
                        WHEN thscode LIKE '920%.BJ' THEN 'beijing'
                        WHEN thscode LIKE '000%.SZ' OR thscode LIKE '001%.SZ'
                          OR thscode LIKE '002%.SZ' OR thscode LIKE '003%.SZ'
                          OR thscode LIKE '600%.SH' OR thscode LIKE '601%.SH'
                          OR thscode LIKE '603%.SH' OR thscode LIKE '605%.SH'
                          THEN 'main'
                        ELSE 'unsupported' END AS board
                FROM joined
            ), limits AS (
                SELECT *, CASE
                    WHEN board = 'chinext' AND trade_date >= DATE '2020-08-24' THEN 0.20
                    WHEN board = 'chinext' THEN 0.10
                    WHEN board = 'star' THEN 0.20
                    WHEN board = 'beijing' AND trade_date >= DATE '2021-11-15' THEN 0.30
                    WHEN board = 'main' THEN 0.10
                    ELSE NULL END AS nominal_limit
                FROM regimes
            ), returns AS (
                SELECT *, high_price / NULLIF(prev_close, 0) - 1 AS high_return,
                    close_price / NULLIF(prev_close, 0) - 1 AS close_return,
                    CASE WHEN prev_close < 10 THEN 'low'
                         WHEN prev_close < 30 THEN 'mid' ELSE 'high' END AS price_band,
                    CASE WHEN prev_turnover < 10000000 THEN 'low'
                         WHEN prev_turnover < 100000000 THEN 'mid' ELSE 'high' END AS liquidity_band
                FROM limits
            )
            SELECT thscode, trade_date, date_ms, prev_date_ms, board,
                   history_bars, prev_close, prev_turnover, price_band, liquidity_band,
                   corporate_action_on_date,
                   open_price, high_price, low_price, close_price, volume, turnover,
                   nominal_limit, high_return, close_return,
                   CASE WHEN history_bars <= {MIN_HISTORY_BARS} OR nominal_limit IS NULL
                             OR corporate_action_on_date
                             OR prev_close IS NULL OR prev_close <= 0
                             OR high_price < low_price OR close_price <= 0
                             OR high_return > 0.45 OR high_return < -0.45
                        THEN 'unclassifiable'
                        WHEN close_return >= nominal_limit - 0.003
                        THEN 'close_at_nominal_limit_proxy'
                        WHEN high_return >= nominal_limit - 0.003
                        THEN 'touched_nominal_limit_proxy'
                        WHEN high_return >= nominal_limit * 0.65
                        THEN 'near_nominal_limit_proxy'
                        ELSE 'other_observed_bar' END AS proxy_class,
                   CASE WHEN board = 'main' AND trade_date < DATE '2026-07-06'
                        THEN 'historical_st_unknown'
                        WHEN board = 'beijing' THEN 'listing_status_unknown'
                        ELSE 'listing_exemption_unknown' END AS regime_gap,
                   'reconstructed_after_close_not_pit_certified' AS evidence_time_status
            FROM returns
        """)
        # A complete observed-bar denominator, including ordinary negatives and
        # rows we cannot classify. Parquet is local, ignored, and never promoted.
        con.execute(
            f"COPY (SELECT * FROM observations ORDER BY trade_date, thscode) "
            f"TO {_quote(output_dir / 'exploration_observations.parquet')} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        summary_rows = con.execute("""
            SELECT year(trade_date) AS year, board, proxy_class, regime_gap,
                   count(*) AS bars, count(distinct trade_date) AS trade_days,
                   count(distinct thscode) AS symbols
            FROM observations GROUP BY ALL ORDER BY year, board, proxy_class, regime_gap
        """).fetchall()
        summary = [dict(zip(("year", "board", "proxy_class", "regime_gap",
                             "bars", "trade_days", "symbols"), row)) for row in summary_rows]
        # Paired controls are only a reproducible descriptive comparison. Same
        # date/board + lagged price and turnover buckets; no current outcomes in
        # matching keys. Missing control remains unmatched, never silently dropped.
        con.execute("""
            CREATE TEMP TABLE matched AS
            WITH events AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY trade_date, board, price_band, liquidity_band
                    ORDER BY thscode) AS match_index
                FROM observations
                WHERE proxy_class IN ('close_at_nominal_limit_proxy',
                                      'touched_nominal_limit_proxy',
                                      'near_nominal_limit_proxy')
            ), controls AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY trade_date, board, price_band, liquidity_band
                    ORDER BY thscode) AS match_index
                FROM observations WHERE proxy_class = 'other_observed_bar'
            )
            SELECT e.thscode AS event_code, e.trade_date, e.proxy_class AS event_class,
                   e.board, e.price_band, e.liquidity_band,
                   c.thscode AS control_code, c.proxy_class AS control_class,
                   CASE WHEN c.thscode IS NULL THEN 'unmatched'
                        ELSE 'date_board_lag_price_liquidity_bucket' END AS match_status
            FROM events e LEFT JOIN controls c
              ON e.trade_date = c.trade_date AND e.board = c.board
             AND e.price_band = c.price_band
             AND e.liquidity_band = c.liquidity_band
             AND e.match_index = c.match_index
        """)
        con.execute(
            f"COPY (SELECT * FROM matched ORDER BY trade_date, event_code) "
            f"TO {_quote(output_dir / 'matched_controls.parquet')} "
            "(FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        total, eligible, ambiguous = con.execute("""
            SELECT count(*), count(*) FILTER (WHERE proxy_class <> 'unclassifiable'),
                   count(*) FILTER (WHERE regime_gap = 'historical_st_unknown')
            FROM observations
        """).fetchone()
        events, unmatched = con.execute("""
            SELECT count(*), count(*) FILTER (WHERE match_status = 'unmatched') FROM matched
        """).fetchone()
        if sum(row["bars"] for row in summary) != total or events < unmatched:
            raise AssertionError("denominator/control reconciliation failed")
        manifest = {
            "protocol": PROTOCOL,
            "source": "local_marketdb_daily_k_ths_dump",
            "source_sha256": sha256_file(db_path),
            "source_rows_all_periods": source_bounds[0],
            "source_first_date_ms": source_bounds[1],
            "source_last_date_ms": source_bounds[2],
            "exploration_end": EXPLORATION_END.isoformat(),
            "validation_start": VALIDATION_START.isoformat(),
            "holdout_start": HOLDOUT_START.isoformat(),
            "validation_or_holdout_outcomes_queried": False,
            "exploration_bars": total,
            "classified_bars": eligible,
            "historical_st_unknown_bars": ambiguous,
            "proxy_events": events,
            "unmatched_proxy_events": unmatched,
            "matching": "same date, board, lagged price band and lagged turnover band; no mcap/volatility/theme match",
            "point_in_time_certified": False,
            "official_limit_up_certified": False,
            "tradability_certified": False,
            "research_use_only": True,
            "outputs": ["exploration_observations.parquet", "matched_controls.parquet", "summary.json"],
        }
        (output_dir / "summary.json").write_text(
            json.dumps({"manifest": manifest, "strata": summary}, ensure_ascii=False,
                       indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return manifest
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.db, args.output), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
