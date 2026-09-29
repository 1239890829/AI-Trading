"""Reproduce RSH-031's price-only early screen and source-pool board paths.

Inputs are frozen outputs of rsh031_asof_audit.py and archived vendor pools.
This describes source outcomes; it does not estimate alpha or executable fills.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import pandas as pd


KEY = ["trade_date", "thscode"]
WINDOWS = {"development": (0, 160), "temporal_check": (160, 220),
           "recent_check": (220, 237)}


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_asof(folder: Path) -> pd.DataFrame:
    report = json.loads((folder / "report.json").read_text())
    candidates_path, labels_path = folder / "candidates.parquet", folder / "source-labels.parquet"
    if (sha256(candidates_path) != report["candidates_sha256"]
            or sha256(labels_path) != report["labels_sha256"]):
        raise ValueError(f"as-of artifact hash changed: {folder}")
    candidates = pd.read_parquet(candidates_path)
    labels = pd.read_parquet(labels_path)
    for name, frame in (("candidates", candidates), ("labels", labels)):
        if frame[KEY].isna().any().any() or frame.duplicated(KEY).any():
            raise ValueError(f"{folder}: invalid {name} identity")
    if not labels.label.dropna().isin([0, 1]).all():
        raise ValueError(f"{folder}: invalid source label")
    if candidates[["early_ret", "ret20"]].isna().any().any():
        raise ValueError(f"{folder}: missing 09:55 feature")
    return candidates[KEY + ["early_ret", "ret20"]].merge(
        labels[KEY + ["label"]], on=KEY, how="left", validate="one_to_one")


def screen_period(frame: pd.DataFrame, days: list[str], repetitions: int = 2000) -> dict:
    part = frame[frame.trade_date.isin(days)].copy()
    if sorted(part.trade_date.unique().tolist()) != days:
        raise ValueError("screen period missing a date")
    # Percentile ranking is within the same 09:55 day, with ties retained.
    rank = part.groupby("trade_date").early_ret.rank(pct=True, method="average")
    part["screen"] = rank.gt(0.98)
    counts = []
    for day, rows in part.groupby("trade_date", sort=True):
        inside, outside = rows[rows.screen], rows[~rows.screen]
        counts.append([len(inside), int(inside.label.eq(1).sum()), int(inside.label.isna().sum()),
                       len(outside), int(outside.label.eq(1).sum()), int(outside.label.isna().sum())])
    a = np.asarray(counts, dtype=np.int64)
    total = a.sum(axis=0)
    n, hit, unknown, other_n, other_hit, other_unknown = map(int, total)
    if not n or not other_n:
        raise ValueError("empty screen or comparison denominator")
    rate = hit / n
    other_rate = other_hit / other_n
    rng = np.random.default_rng(31031)
    sample = rng.integers(0, len(days), size=(repetitions, len(days)))
    grouped = a[sample].sum(axis=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        ratios = ((grouped[:, 1] / grouped[:, 0])
                  / (grouped[:, 4] / grouped[:, 3]))
    ratios = ratios[np.isfinite(ratios)]
    return {"days": len(days), "eligible": n + other_n,
            "known_positives": hit + other_hit, "unknown": unknown + other_unknown,
            "screen": {"count": n, "hits": hit, "unknown": unknown,
                       "false_positives": n - hit - unknown,
                       "hit_rate_bounds": [rate, (hit + unknown) / n],
                       "known_positive_recall": hit / (hit + other_hit)
                       if hit + other_hit else None},
            "other": {"count": other_n, "hits": other_hit, "unknown": other_unknown,
                      "hit_rate_bounds": [other_rate, (other_hit + other_unknown) / other_n]},
            "observed_rate_ratio": rate / other_rate if other_rate else None,
            "day_cluster_bootstrap_ratio_95pct": np.quantile(ratios, [0.025, 0.975]).tolist()
            if len(ratios) else None,
            "daily": [{"date": day, "screen_count": int(row[0]), "screen_hits": int(row[1]),
                       "screen_unknown": int(row[2]), "other_count": int(row[3]),
                       "other_hits": int(row[4]), "other_unknown": int(row[5])}
                      for day, row in zip(days, counts)]}


def board_paths(paths: list[Path], calendar_path: Path) -> dict:
    calendar = json.loads(calendar_path.read_text())
    if calendar != sorted(set(calendar)) or len(calendar) < 2:
        raise ValueError("calendar must contain ordered unique sessions")
    pools: dict[tuple[str, str], dict] = {}
    for path in paths:
        for line in path.read_text().splitlines():
            record = json.loads(line)
            day, pool = record["trade_date"], record["pool"]
            if pool not in ("limit-up-pool", "limit-break-pool") or (day, pool) in pools:
                raise ValueError("duplicate or unsupported pool")
            items = {item["thscode"]: item for item in record["items"]}
            if len(items) != record["total"] or len(items) != len(record["items"]):
                raise ValueError("pool total or identity mismatch")
            if pool == "limit-up-pool":
                for item in items.values():
                    count = item["continue_day_cnt"]
                    if (isinstance(count, bool) or not isinstance(count, (int, float))
                            or not np.isfinite(count) or count < 1 or count != int(count)):
                        raise ValueError("invalid board count")
            pools[day, pool] = items
    if {day for day, _ in pools} != set(calendar):
        raise ValueError("pool dates differ from frozen calendar")
    for day in calendar:
        if (day, "limit-up-pool") not in pools or (day, "limit-break-pool") not in pools:
            raise ValueError("both pools required on every session")
        if pools[day, "limit-up-pool"].keys() & pools[day, "limit-break-pool"].keys():
            raise ValueError("up/break pool overlap")
    summary: dict[str, dict[str, Counter]] = defaultdict(lambda: defaultdict(Counter))
    daily = []
    for day, next_day in zip(calendar, calendar[1:]):
        current = pools[day, "limit-up-pool"]
        next_up = pools[next_day, "limit-up-pool"]
        next_break = pools[next_day, "limit-break-pool"]
        outcomes = Counter()
        for symbol, item in current.items():
            count = int(item["continue_day_cnt"])
            stratum = "1" if count == 1 else "2" if count == 2 else "3+"
            seal = item.get("limit_up_time")
            timing = ("unknown" if not isinstance(seal, str)
                      or not re.fullmatch(r"(?:0[9]|1[0-5]):[0-5][0-9]", seal)
                      else "by_10" if seal <= "10:00" else "after_10")
            if symbol in next_up:
                outcome = ("continued" if next_up[symbol]["continue_day_cnt"] == count + 1
                           else "board_count_conflict")
            elif symbol in next_break:
                outcome = "next_day_break_pool"
            else:
                outcome = "next_day_neither_pool"
            summary[stratum]["all"][outcome] += 1
            summary[stratum][timing][outcome] += 1
            outcomes[outcome] += 1
        daily.append({"date": day, "next_date": next_day, "source_up_events": len(current),
                      **dict(outcomes)})
    packed = {}
    for stratum, by_time in summary.items():
        packed[stratum] = {}
        for timing, counter in by_time.items():
            n = sum(counter.values())
            packed[stratum][timing] = {"total": n,
                                       **{key: counter[key] for key in
                                          ("continued", "next_day_break_pool",
                                           "next_day_neither_pool", "board_count_conflict")},
                                       "continuation_fraction_of_all": counter["continued"] / n}
    return {"sessions": len(calendar), "observed_transitions": len(daily),
            "last_date_censored": len(pools[calendar[-1], "limit-up-pool"]),
            "summary": packed, "daily": daily}


def run(old: Path, recent: Path, pools: list[Path], calendar: Path, output: Path) -> dict:
    if output.exists():
        raise FileExistsError("refusing to overwrite research output")
    inputs = [old / "report.json", old / "candidates.parquet", old / "source-labels.parquet",
              recent / "report.json", recent / "candidates.parquet",
              recent / "source-labels.parquet", *pools, calendar]
    before = [sha256(path) for path in inputs]
    frame = pd.concat([read_asof(old), read_asof(recent)], ignore_index=True)
    if frame.duplicated(KEY).any():
        raise ValueError("window overlap")
    days = sorted(frame.trade_date.unique().tolist())
    if len(days) != 237:
        raise ValueError("expected frozen 220+17 research sessions")
    screen = {name: screen_period(frame, days[start:stop])
              for name, (start, stop) in WINDOWS.items()}
    paths = board_paths(pools, calendar)
    if days != json.loads(calendar.read_text()):
        raise ValueError("candidate and pool calendar mismatch")
    if before != [sha256(path) for path in inputs]:
        raise ValueError("research input changed during audit")
    report = {"kind": "rsh031_price_only_research_atlas", "screen_asof": "09:55 Asia/Shanghai",
              "screen_rule": "within-date early_return percentile > 0.98",
              "screen_windows": {name: [days[start], days[stop - 1]]
                                 for name, (start, stop) in WINDOWS.items()},
              "screen": screen, "board_paths": paths,
              "inputs": [{"path": str(path), "sha256": digest}
                         for path, digest in zip(inputs, before)],
              "script_sha256": sha256(Path(__file__)),
              "limits": ["all windows and the 98th-percentile threshold were inspected before publication; no blind test",
                         "vendor first-board and first-hit labels are source proxies, not exchange truth",
                         "source seal time may be end-of-day; board paths are descriptive",
                         "daily identity lacks first-publication/revision proof",
                         "no point-in-time theme/news, cost, order queue, or actual fill evidence"]}
    output.mkdir(parents=True)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old", type=Path, required=True)
    parser.add_argument("--recent", type=Path, required=True)
    parser.add_argument("--pools", type=Path, nargs="+", required=True)
    parser.add_argument("--calendar", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = run(args.old, args.recent, args.pools, args.calendar, args.output)
    print(json.dumps({"screen": {key: {k: value[k] for k in
                            ("days", "screen", "other", "observed_rate_ratio")}
                            for key, value in report["screen"].items()},
                      "board_paths": report["board_paths"]["summary"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
