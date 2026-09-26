"""Read-only, fixed-score RSH-031 comparison on collected recent pools."""
from __future__ import annotations

import argparse
import hashlib
import json
from datetime import date
from pathlib import Path

import duckdb

from scripts.rsh031_recent_pools import PROTOCOL, date_ms, END, START


def _sql_path(path: Path) -> str:
    return "'" + str(path).replace("'", "''") + "'"


def analyze(db_path: Path, collection_dir: Path, output_dir: Path) -> dict:
    if output_dir.exists() and any(output_dir.iterdir()):
        raise FileExistsError(output_dir)
    manifest = json.loads((collection_dir / "manifest.json").read_text())
    pools_path = collection_dir / "pools.jsonl"
    if manifest["protocol"] != PROTOCOL or not manifest["complete"]:
        raise ValueError("pool collection is incomplete or uses another protocol")
    if hashlib.sha256(pools_path.read_bytes()).hexdigest() != manifest["pools_sha256"]:
        raise ValueError("pool digest mismatch")
    events = []
    pool_dates: dict[str, set[str]] = {}
    for line in pools_path.open(encoding="utf-8"):
        row = json.loads(line)
        day, pool = row["trade_date"], row["pool"]
        pool_dates.setdefault(day, set()).add(pool)
        for item in row["items"]:
            events.append((day, item["thscode"], pool,
                           int(item.get("continue_day_cnt") or 0) == 1
                           if pool == "limit-up-pool" else False,
                           int(item.get("continue_day_cnt") or 0)
                           if pool == "limit-up-pool" else None,
                           item.get("is_st") if pool == "limit-up-pool" else None,
                           item.get("is_new") if pool == "limit-up-pool" else None))
    if len(pool_dates) != manifest["trade_days"] or any(len(x) != 2 for x in pool_dates.values()):
        raise ValueError("missing trade day or pool")
    con = duckdb.connect(":memory:")
    con.execute("SET threads=2")
    try:
        con.execute(f"ATTACH {_sql_path(db_path)} AS source (READ_ONLY)")
        con.execute("CREATE TEMP TABLE collection_days(trade_date DATE)")
        con.executemany("INSERT INTO collection_days VALUES (?)",
                        [(day,) for day in sorted(pool_dates)])
        con.execute("""CREATE TEMP TABLE events(
            trade_date DATE, thscode VARCHAR, pool VARCHAR,
            first_board BOOLEAN, board_count INTEGER,
            is_st BOOLEAN, is_new BOOLEAN)""")
        con.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)", events)
        if con.execute("SELECT count(*) FROM events").fetchone()[0] != sum(manifest["rows"].values()):
            raise AssertionError("official event count differs from collection")
        # A global session calendar ensures the 20-session feature history has no
        # symbol-level suspension gap. Target day membership is never queried to
        # construct the denominator: all prior-day eligible symbols remain.
        con.execute("""
            CREATE TEMP TABLE calendar AS
            SELECT date_ms, row_number() OVER (ORDER BY date_ms) AS session_no,
                   lead(date_ms) OVER (ORDER BY date_ms) AS next_ms
            FROM (SELECT DISTINCT date_ms FROM source.daily_k)
        """)
        con.execute("""
            CREATE TEMP TABLE feature_bars AS
            WITH bars AS (
                SELECT k.thscode, k.date_ms, c.session_no, c.next_ms,
                       k.close_price, k.high_price, k.turnover,
                       lag(k.close_price, 20) OVER w AS close20,
                       lag(c.session_no, 20) OVER w AS session20,
                       avg(k.turnover) OVER prior AS avg_turn20,
                       max(k.high_price) OVER prior AS max_high20
                FROM source.daily_k k JOIN calendar c USING (date_ms)
                WHERE k.date_ms >= ? AND k.date_ms < ?
                WINDOW w AS (PARTITION BY k.thscode ORDER BY k.date_ms),
                       prior AS (PARTITION BY k.thscode ORDER BY k.date_ms
                                 ROWS BETWEEN 20 PRECEDING AND 1 PRECEDING)
            )
            SELECT CAST(to_timestamp(next_ms / 1000.0)
                        AT TIME ZONE 'Asia/Shanghai' AS DATE) AS trade_date,
                   thscode, close_price, turnover,
                   close_price / close20 - 1 AS ret20,
                   turnover / avg_turn20 AS turnover_ratio,
                   close_price > max_high20 AS breakout,
                   CASE WHEN thscode LIKE '300%.SZ' OR thscode LIKE '301%.SZ'
                              OR thscode LIKE '302%.SZ' THEN 'chinext'
                        WHEN thscode LIKE '688%.SH' OR thscode LIKE '689%.SH' THEN 'star'
                        WHEN thscode LIKE '920%.BJ' THEN 'beijing'
                        WHEN thscode LIKE '000%.SZ' OR thscode LIKE '001%.SZ'
                          OR thscode LIKE '002%.SZ' OR thscode LIKE '003%.SZ'
                          OR thscode LIKE '600%.SH' OR thscode LIKE '601%.SH'
                          OR thscode LIKE '603%.SH' OR thscode LIKE '605%.SH'
                          THEN 'main' ELSE 'unsupported' END AS board
            FROM bars
            WHERE session20 = session_no - 20 AND close_price > 0
              AND close20 > 0 AND turnover > 0 AND avg_turn20 > 0
              AND next_ms >= ? AND next_ms <= ?
        """, [date_ms(date(2025, 7, 1)),
              date_ms(date(2026, 9, 1)),
              date_ms(START), date_ms(END)])
        con.execute("""
            CREATE TEMP TABLE samples AS
            SELECT f.*, CASE WHEN e.pool = 'limit-up-pool' AND e.first_board
                                   THEN 1 ELSE 0 END AS first_board,
                   CASE WHEN e.pool IS NULL THEN 'other_observed'
                        ELSE e.pool END AS outcome,
                   CASE WHEN f.trade_date <= DATE '2026-03-31' THEN 'exploration'
                        WHEN f.trade_date <= DATE '2026-05-31' THEN 'validation'
                        ELSE 'holdout' END AS segment,
                   f.ret20 + 0.02 * ln(f.turnover_ratio)
                     + CASE WHEN f.breakout THEN 0.03 ELSE 0 END AS combo_score
            FROM feature_bars f LEFT JOIN events e
              ON f.trade_date = e.trade_date AND f.thscode = e.thscode
            -- The source-returned pools contain SH/SZ symbols only in this
            -- frozen collection. BJ rows would become false negatives.
            WHERE f.board NOT IN ('unsupported', 'beijing')
        """)
        duplicates = con.execute("""
            SELECT count(*) - count(DISTINCT (trade_date, thscode)) FROM samples
        """).fetchone()[0]
        if duplicates:
            raise ValueError(f"duplicate sample identity: {duplicates}")
        counts = con.execute("""
            SELECT segment, count(*) AS denominator, sum(first_board) AS first_boards,
                   count(*) FILTER (WHERE outcome = 'limit-up-pool') AS all_up,
                   count(*) FILTER (WHERE outcome = 'limit-break-pool') AS all_break,
                   count(distinct trade_date) AS days
            FROM samples GROUP BY segment ORDER BY segment
        """).fetchall()
        coverage = con.execute("""
            SELECT pool, count(*) AS official_rows,
                   count(*) FILTER (WHERE s.thscode IS NOT NULL) AS matched_prior_universe,
                   count(*) FILTER (WHERE e.first_board) AS source_first_boards,
                   count(*) FILTER (WHERE e.is_st) AS source_st,
                   count(*) FILTER (WHERE e.is_new) AS source_new,
                   count(*) FILTER (WHERE e.first_board AND e.is_st) AS first_board_st,
                   count(*) FILTER (WHERE e.first_board AND e.is_new) AS first_board_new
            FROM events e LEFT JOIN samples s
              ON e.trade_date = s.trade_date AND e.thscode = s.thscode
            GROUP BY pool ORDER BY pool
        """).fetchall()
        con.execute("""
            CREATE TEMP TABLE ranks AS
            WITH scores AS (
                SELECT segment, trade_date, thscode, first_board,
                       'relative_strength' AS model, ret20 AS score FROM samples
                UNION ALL
                SELECT segment, trade_date, thscode, first_board,
                       'fixed_combination', combo_score FROM samples
            ), daily AS (
                SELECT *, row_number() OVER (
                    PARTITION BY model, trade_date ORDER BY score DESC, thscode
                ) AS day_rank FROM scores
            ), pooled AS (
                SELECT *, row_number() OVER (
                    PARTITION BY model, segment ORDER BY score DESC, trade_date, thscode
                ) AS global_rank,
                sum(first_board) OVER (
                    PARTITION BY model, segment ORDER BY score DESC, trade_date, thscode
                    ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                ) AS cum_positive FROM daily
            ) SELECT * FROM pooled
        """)
        metrics = con.execute("""
            SELECT segment, model, count(*) AS denominator, sum(first_board) AS positives,
                   sum(CASE WHEN day_rank <= 20 THEN first_board ELSE 0 END) AS top20_hits,
                   count(*) FILTER (WHERE day_rank <= 20) AS top20_slots,
                   sum(CASE WHEN first_board = 1
                            THEN cum_positive::DOUBLE / global_rank ELSE 0 END)
                       / nullif(sum(first_board), 0) AS average_precision
            FROM ranks GROUP BY segment, model ORDER BY segment, model
        """).fetchall()
        # Descriptive matched controls use only prior-day board, close price,
        # and turnover buckets. Missing pairs are reported, never dropped.
        matched = con.execute("""
            WITH binned AS (
                SELECT *, CASE WHEN close_price < 10 THEN 'low'
                               WHEN close_price < 30 THEN 'mid' ELSE 'high' END AS price_band,
                          CASE WHEN turnover < 10000000 THEN 'low'
                               WHEN turnover < 100000000 THEN 'mid' ELSE 'high' END
                               AS liquidity_band
                FROM samples
            ), pos AS (
                SELECT *, row_number() OVER (
                    PARTITION BY trade_date, board, price_band, liquidity_band
                    ORDER BY thscode) AS match_no
                FROM binned WHERE first_board = 1
            ), neg AS (
                SELECT *, row_number() OVER (
                    PARTITION BY trade_date, board, price_band, liquidity_band
                    ORDER BY thscode) AS match_no
                FROM binned WHERE outcome = 'other_observed'
            )
            SELECT count(*) AS first_boards,
                   count(*) FILTER (WHERE neg.thscode IS NULL) AS unmatched
            FROM pos LEFT JOIN neg USING
                (trade_date, board, price_band, liquidity_band, match_no)
        """).fetchone()
        board_counts = con.execute("""
            SELECT segment, board, count(*) AS stock_days,
                   sum(first_board) AS first_boards,
                   count(*) FILTER (WHERE outcome = 'limit-break-pool') AS breaks
            FROM samples GROUP BY segment, board ORDER BY segment, board
        """).fetchall()
        source_progression = con.execute("""
            WITH days AS (
                SELECT trade_date,
                       lead(trade_date) OVER (ORDER BY trade_date) AS next_day
                FROM collection_days
            ), firsts AS (
                SELECT e.trade_date, e.thscode, d.next_day
                FROM events e JOIN days d USING (trade_date)
                WHERE e.pool = 'limit-up-pool' AND e.first_board
                  AND d.next_day IS NOT NULL
            )
            SELECT count(*) AS first_boards_with_next_day,
                   count(*) FILTER (WHERE nxt.pool = 'limit-up-pool'
                                         AND nxt.board_count >= 2) AS next_day_continued,
                   count(*) FILTER (WHERE nxt.pool = 'limit-up-pool'
                                         AND nxt.board_count < 2) AS next_day_up_reset,
                   count(*) FILTER (WHERE nxt.pool = 'limit-break-pool') AS next_day_break,
                   count(*) FILTER (WHERE nxt.pool IS NULL) AS next_day_not_in_either_pool
            FROM firsts f LEFT JOIN events nxt
              ON nxt.trade_date = f.next_day AND nxt.thscode = f.thscode
        """).fetchone()
        if source_progression[0] != sum(source_progression[1:]):
            raise AssertionError("first-board next-day outcomes do not reconcile")
        max_boards = con.execute("""
            SELECT max(board_count),
                   count(*) FILTER (WHERE board_count >= 5)
            FROM events WHERE pool = 'limit-up-pool'
        """).fetchone()
        output_dir.mkdir(parents=True, exist_ok=True)
        con.execute(f"""COPY (
            SELECT * FROM samples ORDER BY trade_date, thscode
        ) TO {_sql_path(output_dir / 'samples.parquet')}
        (FORMAT PARQUET, COMPRESSION ZSTD)""")
        result = {
            "protocol": "rsh031-recent-analysis-v1",
            "collection_sha256": manifest["pools_sha256"],
            "source_scope": "THS SH/SZ post-close pools + retrospectively stored local raw daily_k",
            "excluded_board": "beijing: source pool returned no BJ symbols; cannot label negatives",
            "denominator": [dict(zip(("segment", "stock_days", "first_boards", "all_up",
                                       "all_break", "trade_days"), row)) for row in counts],
            "official_event_join": [dict(zip(("pool", "official_rows", "matched_prior_universe",
                                               "source_first_boards", "source_st", "source_new",
                                               "first_board_st", "first_board_new"), row))
                                    for row in coverage],
            "metrics": [dict(zip(("segment", "model", "stock_days", "positives", "top20_hits",
                                   "top20_slots", "average_precision"), row)) |
                        {"top20_precision": row[4] / row[5],
                         "top20_recall": row[4] / row[3],
                         "top20_false_positives": row[5] - row[4],
                         "prevalence": row[3] / row[2]}
                        for row in metrics],
            "by_board": [dict(zip(("segment", "board", "stock_days", "first_boards",
                                   "breaks"), row)) for row in board_counts],
            "source_progression": dict(zip(("first_boards_with_next_day", "next_day_continued",
                                            "next_day_up_reset", "next_day_break",
                                            "next_day_not_in_either_pool"),
                                           source_progression)),
            "source_max_board_count": max_boards[0],
            "source_five_plus_board_rows": max_boards[1],
            "matched_first_boards": matched[0], "unmatched_first_boards": matched[1],
            "point_in_time_text_certified": False, "tradable_entry_certified": False,
            "outcome_day_used_for_denominator": False,
            "sample_sha256": hashlib.sha256((output_dir / "samples.parquet").read_bytes()).hexdigest(),
        }
        (output_dir / "analysis.json").write_text(
            json.dumps(result, ensure_ascii=False, sort_keys=True, indent=2) + "\n",
            encoding="utf-8")
        return result
    finally:
        con.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--collection", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(analyze(args.db, args.collection, args.output),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
