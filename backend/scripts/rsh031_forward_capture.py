"""Capture short-window RSH-031 evidence at receipt time with free providers.

Run `morning` around 09:55 and `close` after 15:20 Beijing time. A `probe`
checks source availability without creating evidence. This script only writes
ignored research artifacts; it never changes production signals or trades.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import re
from datetime import datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from app.data_providers.eastmoney import EastmoneyProvider
from app.data_providers.tencent import TencentProvider


BJ = ZoneInfo("Asia/Shanghai")
SYMBOL = re.compile(r"[0-9]{6}\.(?:SH|SZ)\Z")
POOLS = {"limit-up-pool": "getTopicZTPool", "limit-break-pool": "getTopicZBPool"}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def universe(path: Path) -> list[dict]:
    source = pd.read_parquet(path, columns=["Symbol", "rs_hyname"])
    source = source[source.Symbol.astype(str).str.fullmatch(SYMBOL.pattern)].copy()
    if source.Symbol.isna().any() or source.Symbol.duplicated().any() or len(source) < 4000:
        raise ValueError("invalid frozen SH/SZ universe")
    return [{"thscode": row.Symbol, "industry": row.rs_hyname
             if isinstance(row.rs_hyname, str) and row.rs_hyname else None}
            for row in source.itertuples(index=False)]


def validate_clock(now: datetime, mode: str) -> None:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("capture clock must include timezone")
    current = now.astimezone(BJ)
    start, end = ((time(9, 55), time(10, 0)) if mode == "morning"
                  else (time(15, 20), time(16, 30)))
    if current.weekday() >= 5 or not start <= current.time() <= end:
        raise ValueError(f"{mode} collection outside Beijing capture window")


async def quote_rows(symbols: list[str], provider: TencentProvider,
                     *, batch_size: int = 80, concurrency: int = 6) -> tuple[list[dict], list[dict]]:
    limiter = asyncio.Semaphore(concurrency)

    async def one(batch: list[str]) -> tuple[list[dict], dict | None]:
        async with limiter:
            try:
                quotes = await provider.get_quotes([symbol[:6] for symbol in batch])
                received = datetime.now(timezone.utc).isoformat()
                by_symbol = {f"{quote.symbol}.{quote.market}": quote for quote in quotes}
                if len(by_symbol) != len(quotes) or not set(by_symbol).issubset(batch):
                    raise ValueError("quote source returned duplicate or unrequested identity")
                return ([{"thscode": symbol, "received_at": received,
                          "source_at": quote.data_timestamp.isoformat()
                          if (quote := by_symbol.get(symbol)) and quote.data_timestamp else None,
                          "price": quote.price if quote else None,
                          "prev_close": quote.prev_close if quote else None,
                          "name": quote.name if quote else None,
                          "source": "tencent" if quote else None} for symbol in batch], None)
            except Exception as exc:
                received = datetime.now(timezone.utc).isoformat()
                return ([{"thscode": symbol, "received_at": received, "source_at": None,
                          "price": None, "prev_close": None, "name": None, "source": None}
                         for symbol in batch],
                        {"first_symbol": batch[0], "count": len(batch), "error": type(exc).__name__})

    batches = [symbols[index:index + batch_size] for index in range(0, len(symbols), batch_size)]
    results = await asyncio.gather(*(one(batch) for batch in batches))
    rows = [row for batch, _ in results for row in batch]
    errors = [error for _, error in results if error]
    if len(rows) != len(symbols) or len({row["thscode"] for row in rows}) != len(symbols):
        raise ValueError("quote collection lost universe identity")
    return rows, errors


def prepare_morning(rows: list[dict], members: list[dict],
                    day: str, decision_at: datetime) -> tuple[list[dict], dict]:
    by_symbol = {row["thscode"]: row for row in rows}
    if len(by_symbol) != len(rows) or set(by_symbol) != {member["thscode"] for member in members}:
        raise ValueError("quote rows differ from frozen universe")
    result = []
    for member in members:
        row = dict(by_symbol[member["thscode"]])
        stamp = datetime.fromisoformat(row["source_at"]) if row["source_at"] else None
        received = datetime.fromisoformat(row["received_at"])
        fresh = (stamp is not None and stamp.astimezone(BJ).date().isoformat() == day
                 and stamp <= received <= decision_at
                 and 0 <= (received - stamp).total_seconds() <= 120
                 and 0 <= (decision_at - stamp).total_seconds() <= 120)
        price, prev = row["price"], row["prev_close"]
        st = bool(re.match(r"\*?ST", (row["name"] or "").upper()))
        valid_price = (isinstance(price, (int, float)) and price > 0
                       and isinstance(prev, (int, float)) and prev > 0)
        reason = ("st_name" if st else "missing_or_stale_quote" if not fresh
                  else "invalid_price" if not valid_price else None)
        row.update(industry=member["industry"], usable=reason is None, exclusion=reason)
        row["early_return"] = price / prev - 1 if row["usable"] else None
        result.append(row)
    usable = sum(row["usable"] for row in result)
    return result, {"universe": len(result), "usable": usable,
                    "unusable": len(result) - usable, "coverage": usable / len(result),
                    "complete_for_research": usable / len(result) >= 0.95}


async def fetch_pool(provider: EastmoneyProvider, day: str, pool: str) -> dict:
    payload = await provider._get_json(
        "https://push2ex.eastmoney.com/" + POOLS[pool],
        {"ut": "7eea3edcaed734bea9cbfc24409ed989", "dpt": "wz.ztzt",
         "Pageindex": "0", "pagesize": "500", "sort": "fbt:asc", "date": day.replace("-", "")})
    data = payload.get("data")
    if payload.get("rc") != 0 or not isinstance(data, dict):
        raise ValueError("pool source business response failed")
    if str(data.get("qdate")) != day.replace("-", ""):
        raise ValueError("pool source date mismatch")
    items = data.get("pool")
    total = data.get("tc")
    if (not isinstance(items, list) or type(total) is not int
            or len(items) != total or total > 500):
        raise ValueError("pool pagination or count mismatch")
    codes = [item.get("c") for item in items]
    if any(not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code)
           for code in codes) or len(set(codes)) != len(codes):
        raise ValueError("pool symbol identity mismatch")
    return {"pool": pool, "trade_date": day, "total": total,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "items": [{"symbol": item["c"], "first_seal_raw": item.get("fbt"),
                       "board_count": item.get("lbc") if pool == "limit-up-pool" else None}
                      for item in items]}


def write_once(path: Path, contents: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as target:
        target.write(contents)


async def run(mode: str, universe_path: Path, output: Path, probe: bool) -> dict:
    start = datetime.now(BJ)
    if not probe:
        validate_clock(start, mode)
    day = start.date().isoformat()
    folder = output / day
    if not probe and any((folder / name).exists() for name in
                         (("morning.json", "morning.jsonl") if mode == "morning"
                          else ("close.json", "close-pools.json"))):
        raise FileExistsError("research day already captured")
    if mode == "morning":
        members = universe(universe_path)
        if probe:
            members = members[:100]
        provider = TencentProvider(timeout=8)
        try:
            rows, errors = await quote_rows([member["thscode"] for member in members], provider)
        finally:
            await provider.aclose()
        pool_provider = EastmoneyProvider(timeout=8)
        try:
            early_pool = await fetch_pool(pool_provider, day, "limit-up-pool")
            pool_error = None
        except Exception as exc:
            early_pool = None
            pool_error = type(exc).__name__
        finally:
            await pool_provider.aclose()
        decision_at = datetime.now(timezone.utc)
        if not probe:
            validate_clock(decision_at, mode)
        prepared, coverage = prepare_morning(rows, members, day, decision_at)
        receipt = {"mode": mode, "trade_date": day, "decision_asof": decision_at.isoformat(),
                   "captured_started_at": start.isoformat(), "source": "tencent_free_snapshot",
                   "universe_sha256": sha256(universe_path), "coverage": coverage,
                   "batch_errors": errors, "early_pool": early_pool,
                   "early_pool_error": pool_error, "probe": probe}
        if not probe:
            file = folder / "morning.jsonl"
            write_once(file, "".join(json.dumps(row, ensure_ascii=False) + "\n"
                                     for row in prepared))
            receipt["rows_sha256"] = sha256(file)
            write_once(folder / "morning.json", json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        return receipt
    provider = EastmoneyProvider(timeout=8)
    try:
        pools = [await fetch_pool(provider, day, pool) for pool in POOLS]
    finally:
        await provider.aclose()
    if not probe:
        validate_clock(datetime.now(BJ), mode)
    if {row["symbol"] for row in pools[0]["items"]} & {row["symbol"] for row in pools[1]["items"]}:
        raise ValueError("close pools overlap")
    receipt = {"mode": mode, "trade_date": day, "captured_started_at": start.isoformat(),
               "captured_finished_at": datetime.now(BJ).isoformat(),
               "source": "eastmoney_free_source_pools", "counts": {p["pool"]: p["total"] for p in pools},
               "probe": probe}
    if not probe:
        file = folder / "close-pools.json"
        write_once(file, json.dumps(pools, ensure_ascii=False, indent=2) + "\n")
        receipt["pools_sha256"] = sha256(file)
        write_once(folder / "close.json", json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return receipt


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["morning", "close"])
    parser.add_argument("--universe", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--probe", action="store_true", help="read source without writing evidence")
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.mode, args.universe, args.output, args.probe)),
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
