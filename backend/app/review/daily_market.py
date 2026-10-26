"""Read completed, date-bound marketdb bars without synchronizing or trading."""
from __future__ import annotations

from collections import Counter
from datetime import date, datetime, time, timedelta
import hashlib
import json
from pathlib import Path

from app.core.bjtime import BJ_TZ, beijing_now
from app.market.marketdb_freshness import DEFAULT_DB_PATH, _to_date
from app.picks._strategy_reference_rules import is_reference_stock_code
from app.review.provenance import evidence, finite, source_time
from app.review.schemas import SectionProvenance


_BASIS = "复权日线对照，非官方实时涨跌幅"


def _digest(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def _identity(path: Path) -> tuple:
    value = path.stat()
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns


def _document(path: Path) -> dict:
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("日线同步或质量记录不可用")
    value = json.loads(path.read_text())
    if not isinstance(value, dict):
        raise ValueError("日线同步或质量记录格式错误")
    return value


def _bounds(day: date) -> tuple[int, int]:
    start = datetime.combine(day, time(), tzinfo=BJ_TZ)
    return int(start.timestamp() * 1000), int((start + timedelta(days=1)).timestamp() * 1000)


def daily_source_wait_reason(now: datetime, *, enabled: bool, sync_hour: int = 16,
                             sync_minute: int = 30, sync_interval_seconds: float = 300,
                             db_path: Path | None = None) -> str | None:
    """Cheap bounded dependency check; never open the warehouse or start sync.

    Call only for a calendar-confirmed trading day with no saved report. Existing
    source scheduling defines the deadline; this does not change its cadence.
    """
    checked = source_time(now)
    interval = finite(sync_interval_seconds)
    if (not enabled or checked is None or interval is None or interval <= 0
            or type(sync_hour) is not int or not 0 <= sync_hour < 24
            or type(sync_minute) is not int or not 0 <= sync_minute < 60):
        return None
    completed = checked.replace(hour=16, minute=0, second=0, microsecond=0)
    try:
        deadline = max(completed, checked.replace(hour=sync_hour, minute=sync_minute,
                                                 second=0, microsecond=0) + timedelta(seconds=interval * 2))
    except (OverflowError, ValueError):
        return None
    # An unusable schedule must not defer today's report into tomorrow, when
    # the scheduler deliberately does not backfill yesterday.
    if deadline.date() != checked.date() or checked >= deadline:
        return None
    path = Path(db_path or DEFAULT_DB_PATH)
    if path.is_symlink() or not path.is_file():
        return None
    prefix = f"等待已启用日线来源（最晚{deadline:%Y-%m-%d %H:%M}保留缺失生成）"
    if checked < completed:
        return prefix + "：尚未到已完成日线读取时点"
    try:
        quality = _document(path.parent / "quality_report.json")
        state = _document(path.parent / "sync_state.json")
        synced, marked = source_time(quality.get("synced_at")), source_time(state.get("at"))
        latest = _to_date(((quality.get("sync") or {}).get("daily") or {}).get("dmax"))
        if (quality.get("available") is True and quality.get("status") in {"ok", "warn"}
                and quality.get("error_count") == 0
                and not any(item.get("severity") == "error" for item in quality.get("issues", []))
                and synced and marked and completed <= synced <= checked and completed <= marked <= checked
                and state.get("last_sync_date") == checked.strftime("%Y%m%d")
                and latest == checked.date()):
            return None
    except Exception:
        pass
    return prefix + "：原同步或质量记录尚未证明当天成功"


def _read_rows(path: Path, day: date, previous: date, sync: dict) -> tuple[list, list]:
    import duckdb

    with duckdb.connect(str(path), read_only=True) as con:
        con.execute("BEGIN TRANSACTION")
        maximum, total = con.execute("SELECT max(date_ms), count(*) FROM daily_k").fetchone()
        adj_total = con.execute("SELECT count(*) FROM daily_k_adj").fetchone()[0]
        daily, adjusted = sync.get("daily") or {}, sync.get("adj") or {}
        if (daily.get("dmax") != maximum or daily.get("total") != total
                or adjusted.get("rows") != adj_total):
            raise ValueError("日线仓与原同步质量记录的版本不符")
        start, end = _bounds(day)
        raw = con.execute("SELECT thscode, date_ms, close_price, volume, turnover FROM daily_k "
                          "WHERE date_ms >= ? AND date_ms < ?", [start, end]).fetchall()
        previous_start, previous_end = _bounds(previous)
        adj = con.execute("SELECT thscode, date_ms, close_adj FROM daily_k_adj "
                          "WHERE (date_ms >= ? AND date_ms < ?) OR (date_ms >= ? AND date_ms < ?)",
                          [previous_start, previous_end, start, end]).fetchall()
    return raw, adj


def collect_daily_breadth(day: date, *, trade_days: list[date], db_path: Path | None = None,
                          now: datetime | None = None) -> tuple[dict, SectionProvenance, dict]:
    """Return a finite stored-bar comparison; missing daily facts are not zeroes.

    The caller supplies its existing official calendar. A source day label is not
    a source publication time. This function never calls the network or a model,
    writes a source, changes live freshness, or infers exact limits/suspensions.
    """
    now = source_time(now or beijing_now())
    metadata: dict = {"basis": _BASIS, "source_day": day.isoformat(),
                      "source_as_of": None, "source_clock_semantics": "日线日期标签；首次发布时间未知",
                      "limit_counts": "未从复权涨跌推断涨跌停、新股或停牌数"}
    try:
        if now is None or not all(type(value) is date for value in trade_days):
            raise ValueError("缺有效带时区运行钟或官方交易日历")
        days = sorted(set(trade_days))
        if len(days) != len(trade_days) or day not in days or days.index(day) == 0:
            raise ValueError("日历不能证明报告日及其相邻前交易日")
        if day > now.date() or day == now.date() and now.time().replace(tzinfo=None) < time(16):
            raise ValueError("尚未达到现役日线读取的已完成日界限")
        previous = days[days.index(day) - 1]
        path = Path(db_path or DEFAULT_DB_PATH)
        if path.is_symlink() or not path.is_file():
            raise ValueError("已有日线仓不可用；未触发下载或同步")
        quality_path, state_path = path.parent / "quality_report.json", path.parent / "sync_state.json"
        database_identity = _identity(path)
        before = {"quality": _digest(quality_path), "sync_state": _digest(state_path)}
        quality, state = _document(quality_path), _document(state_path)
        synced = source_time(quality.get("synced_at"))
        marked = source_time(state.get("at"))
        state_day = str(state.get("last_sync_date") or "")
        if (quality.get("available") is not True or quality.get("status") not in {"ok", "warn"}
                or quality.get("error_count") != 0
                or any(item.get("severity") == "error" for item in quality.get("issues", []))):
            raise ValueError("原日线质量记录未通过")
        if (not synced or not marked or synced > now or marked > now
                or synced.date() < day or marked.date() < day
                or state_day != marked.strftime("%Y%m%d")
                or synced.date() != marked.date()
                or synced.time().replace(tzinfo=None) < time(16)
                or marked.time().replace(tzinfo=None) < time(16)):
            raise ValueError("原同步状态未证明该日已完成同步")
        sync = quality.get("sync") or {}
        latest = _to_date((sync.get("daily") or {}).get("dmax"))
        if latest is None or latest < day or latest > now.date():
            raise ValueError("日线源内容日期缺失、滞后或在未来")
        raw, adjusted = _read_rows(path, day, previous, sync)
        if not raw:
            raise ValueError("已有日线仓无该日源记录；不回落其他日期")
        raw_keys = [(symbol, _to_date(ms)) for symbol, ms, *_ in raw]
        adj_keys = [(symbol, _to_date(ms)) for symbol, ms, _ in adjusted]
        if len(set(raw_keys)) != len(raw_keys) or len(set(adj_keys)) != len(adj_keys):
            raise ValueError("该日或相邻日源主键重复，未取首条或合计重复记录")
        values = {(symbol, _to_date(ms)): finite(value) for symbol, ms, value in adjusted}
        warned = set()
        for issue in quality.get("issues", []):
            if issue.get("check") == "daily_k_adj.single_day_jump":
                for sample in issue.get("sample", []):
                    if isinstance(sample, list) and len(sample) >= 2:
                        warned.add((sample[0], _to_date(sample[1])))
        counts: Counter = Counter()
        missing: Counter = Counter()
        amount, amount_rows, universe = 0.0, 0, 0
        for symbol, ms, close, volume, turnover in raw:
            if not is_reference_stock_code(symbol):
                counts["excluded_non_stock"] += 1
                continue
            universe += 1
            value, prior = values.get((symbol, day)), values.get((symbol, previous))
            close, volume, turnover = finite(close), finite(volume), finite(turnover)
            raw_valid = close is not None and close > 0 and volume is not None and volume >= 0
            if (raw_valid and turnover is not None and turnover >= 0
                    and finite(amount + turnover) is not None):
                amount += turnover
                amount_rows += 1
            if not raw_valid:
                missing["invalid_raw_bar"] += 1
            elif value is None or value <= 0:
                missing["missing_adjusted_close"] += 1
            elif prior is None or prior <= 0:
                missing["missing_previous_adjacent_bar"] += 1
            elif (symbol, day) in warned or (symbol, previous) in warned:
                missing["source_quality_warning"] += 1
            else:
                pct = (value / prior - 1) * 100
                if finite(pct) is None:
                    missing["invalid_adjusted_change"] += 1
                else:
                    counts["up" if pct > 0 else "down" if pct < 0 else "flat"] += 1
        after = {"quality": _digest(quality_path), "sync_state": _digest(state_path)}
        if before != after or database_identity != _identity(path):
            raise ValueError("读取期间日线、复权或质量版本发生变化；未发布混版")
        valid = counts["up"] + counts["down"] + counts["flat"]
        if not universe or not valid:
            raise ValueError("已有日线没有可核的A股相邻日对照样本")
        coverage = {"verified_count": valid, "universe_count": universe, "missing_count": universe - valid}
        # Hash only the two-day input captured in the same read-only transaction,
        # not unrelated multi-year history. Source values (including rejected
        # NaN tokens) participate in this evidence digest but are never published.
        input_hash = hashlib.sha256(json.dumps(
            {"raw": sorted(raw, key=repr), "adjusted": sorted(adjusted, key=repr)},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        source_hashes = {**before, "daily_inputs": input_hash}
        metadata.update(source_hashes=source_hashes, database_size_bytes=database_identity[2],
                        source_hash_scope="同一只读事务中当日日线及相邻两日复权输入；非无关全库",
                        previous_trade_date=previous.isoformat(), coverage=coverage,
                        missing_reasons=dict(missing), excluded_non_stock=counts["excluded_non_stock"],
                        source_synced_at=synced.isoformat(), observed_at=now.isoformat(), quality_status=quality["status"],
                        amount_basis={"field": "daily_k.turnover", "unit": "CNY", "valid_count": amount_rows,
                                      "universe_count": universe, "missing_count": universe - amount_rows},
                        population_basis="源仓该日已有A股代码行；未认证全市场/存续全集", source_completed_date=day.isoformat())
        metadata["source_version"] = hashlib.sha256(json.dumps(
            {"hashes": source_hashes, "day": day.isoformat(), "previous": previous.isoformat(),
             "basis": _BASIS}, sort_keys=True).encode()).hexdigest()
        breadth = {"total": universe, "up": counts["up"], "down": counts["down"], "flat": counts["flat"],
                   "basis": _BASIS, "coverage": coverage, "source_day": day.isoformat(),
                   "amount_basis": f"原始日线成交额（人民币元），有效{amount_rows}/{universe}条；不从复权价格推算金额"}
        if amount_rows:
            breadth["total_amount"] = round(amount, 2)
        reason = (f"{_BASIS}；源仓{valid}/{universe}只相邻交易日可评；"
                  f"成交额{amount_rows}/{universe}条原币CNY汇总；日期标签不是15:00源钟；"
                  "不认证全市场全集，未推断涨跌停或停牌，不能用于混时点情绪判定")
        return breadth, evidence(day, state="partial", source="marketdb.daily_k+daily_k_adj",
                                 received=synced, source_day=day.isoformat(), reason=reason), metadata
    except Exception as exc:
        reason = str(exc)[:350] if isinstance(exc, ValueError) else f"日线只读核验失败：{type(exc).__name__}"
        metadata["unavailable_reason"] = reason
        return {}, evidence(day, source="marketdb.daily_k+daily_k_adj", reason=reason), metadata
