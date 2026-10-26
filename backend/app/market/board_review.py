"""Bounded date-bound price evidence owned by board_flow's existing daily store."""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import date, datetime
import hashlib
import json
import math
import re

from app.core.inflight_reads import read_inflight
from app.core.bjtime import BJ_TZ
from app.market import board_flow as owner


_VERSION = "official_daily_candidate_sample_v1"
_CODE = re.compile(r"[0-9]{6}\.TI")
_EDGE = 10
_RETRY_SECONDS = 60


def _number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def _store() -> dict:
    if not owner._DAILY_STORE.exists():
        return {"days": {}}
    data = json.loads(owner._DAILY_STORE.read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("days"), dict):
        raise ValueError("board owner daily store malformed; original retained")
    return data


def _record(day: date) -> dict | None:
    row = (_store()["days"].get(day.isoformat()) or {}).get("review_source")
    if not isinstance(row, dict) or row.get("contract_version") != _VERSION:
        return None
    value = row.get("payload")
    if (not isinstance(value, dict) or value.get("source_trade_date") != day.isoformat()
            or not isinstance(value.get("boards"), dict) or not isinstance(value.get("summary"), dict)):
        return None
    return deepcopy(row)


def _needs_recovery(record: dict, now: datetime) -> bool:
    payload = record["payload"]
    incomplete = any(not (payload.get("summary", {}).get(kind) or {}).get("candidate_count")
                     or (payload["summary"][kind].get("verified_count", 0)
                         < payload["summary"][kind].get("candidate_count", 0))
                     for kind in ("industry", "concept"))
    if not incomplete:
        return False
    try:
        stamp = datetime.fromisoformat(record.get("attempted_at") or payload.get("received_at") or "")
        return stamp.tzinfo is not None and (now - stamp).total_seconds() >= _RETRY_SECONDS
    except (ValueError, TypeError):
        return False  # unknown attempt time cannot prove the cooldown elapsed


def _empty(day: date, reason: str) -> dict:
    return {"boards": {"industry": [], "concept": []}, "summary": {},
            "source": "ths_official_kline", "source_as_of": None,
            "source_trade_date": day.isoformat(), "received_at": None,
            "state": "unavailable", "reason": reason}


def _save(day: date, result: dict, inputs: dict, expected_version: str | None = None) -> dict:
    with owner._STORE_WRITE_LOCK:
        current = _record(day)
        if current is not None and current.get("frozen_version") != expected_version:
            return current["payload"]
        if current is None and expected_version is not None:
            raise RuntimeError("board review source changed before recovery; not overwritten")
        data = _store()
        record = data["days"].setdefault(day.isoformat(), {"ranks": {}, "boards": {}, "members": {}})
        if current is not None:
            record.setdefault("review_history", []).append(current)
        # Input identity alone does not change when one missing bar recovers.
        # Bind CAS to the actual observation version, retaining the input hash.
        version_input = {"inputs": inputs, "payload": result, "previous_version": expected_version}
        version = hashlib.sha256(json.dumps(version_input, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        result["source_version"] = version
        record["review_source"] = {
            "contract_version": _VERSION,
            "frozen_version": version,
            "input_version": hashlib.sha256(json.dumps(inputs, sort_keys=True, ensure_ascii=False).encode()).hexdigest(),
            "attempted_at": result["received_at"],
            "inputs": inputs,
            "payload": deepcopy(result),
        }
        record["verified_boards"] = deepcopy(result["boards"])
        record["review_summary"] = deepcopy(result["summary"])
        data["days"] = dict(sorted(data["days"].items())[-owner._DAYS_RETENTION:])
        data["updated_at"] = owner.beijing_now().isoformat()
        if not owner._write_json_atomic(owner._DAILY_STORE, data, review_versions={day.isoformat(): expected_version}):
            raise RuntimeError("board review source could not persist; original retained")
        owner._mem_memo["daily"] = (None, {})
    return deepcopy(result)


async def collect_review_boards(provider, day: date, *, catalog_service=None, retry_missing: bool = False) -> dict:
    """Read frozen evidence first; only today's completed session may read sources.

    Per kind, snapshot all official identities in existing batches of 50, then
    verify at most ten apparent strongest and ten weakest with dated daily bars.
    This is an explicitly selected sample, never a whole-market strongest claim.
    """
    try:
        record = _record(day)
    except (OSError, ValueError) as exc:
        return _empty(day, str(exc))
    now = owner.beijing_now()
    if record is not None and (not retry_missing or day != now.date() or not _needs_recovery(record, now)):
        return record["payload"]
    if day != now.date():
        return _empty(day, "原owner该日无已核价格样本；历史复盘未请求当前行情替代")
    if (now.hour, now.minute) < (15, 5):
        return _empty(day, "当日连续交易尚未完成；未把形成中的日线当盘后事实")
    if catalog_service is None:
        return _empty(day, "现役同花顺官方目录服务不可用；未新建来源或猜板块身份")

    async def load():
        current = _record(day)
        if current is not None and (not retry_missing or not _needs_recovery(current, owner.beijing_now())):
            return current["payload"]
        days = await owner.trade_calendar.trading_days(provider)
        earlier = sorted(value for value in days if value < day)
        if day not in days or not earlier:
            return _empty(day, "有效交易日历未覆盖报告日及前交易日；未猜测收益基准")
        previous = earlier[-1]
        expected_version = current.get("frozen_version") if current else None
        result = deepcopy(current["payload"]) if current else _empty(day, "")
        result.pop("source_version", None)
        inputs = deepcopy(current["inputs"]) if current else {
            "day": day.isoformat(), "previous_trade_date": previous.isoformat(), "kinds": {}}
        if inputs.get("previous_trade_date") != previous.isoformat():
            return current["payload"]  # cannot revise successful old evidence under a changed baseline
        for kind in ("industry", "concept"):
            prior = deepcopy(result["summary"].get(kind) or {})
            if prior.get("candidate_count") and prior.get("verified_count") == prior["candidate_count"]:
                continue  # never re-read successful kinds or their bars
            summary = prior or {"source": "ths_official_kline", "ranking_basis": "official_daily_candidate_sample",
                       "universe_count": 0, "snapshot_count": 0, "candidate_count": 0,
                       "verified_count": 0, "missing_count": 0, "limit": _EDGE * 2, "reason": ""}
            result["summary"][kind] = summary
            try:
                frozen = inputs["kinds"].setdefault(kind, {})
                identities = frozen.get("catalog")
                if not identities:
                    directory = await catalog_service.read_board_catalog(kind)
                    items = directory.get("items")
                    if (directory.get("source") != "ths_official" or not isinstance(items, list) or not items
                            or any(not isinstance(row, dict) or not _CODE.fullmatch(str(row.get("code") or ""))
                                   or not isinstance(row.get("name"), str) or not row["name"].strip() for row in items)):
                        raise ValueError("官方目录身份或正式名称不完整")
                    identities = {row["code"]: row["name"] for row in items}
                    if len(identities) != len(items) or directory.get("total") != len(items):
                        raise ValueError("官方目录重复或覆盖不完整")
                    frozen["catalog"] = identities
                codes = sorted(identities)
                summary["universe_count"] = len(codes)
                selected = frozen.get("candidates") or []
                prices = frozen.get("snapshot_changes") or {}
                receipts = frozen.get("snapshot_receipts") or []
                # A frozen candidate set never changes during missing-bar recovery.
                for start in range(0, len(codes) if not selected else 0, 50):
                    wanted = codes[start:start + 50]
                    snapshot = await catalog_service.read_board_snapshots(wanted)
                    if not snapshot.get("available"):
                        continue
                    raw = snapshot.get("rows")
                    if not isinstance(raw, list):
                        raise ValueError("官方批量报价形状不完整")
                    seen = set()
                    for row in raw:
                        code = row.get("code") if isinstance(row, dict) else None
                        if code not in wanted or code in seen:
                            raise ValueError("官方报价证券身份错配或重复")
                        seen.add(code)
                        price, pct = _number(row.get("price")), _number(row.get("change_pct"))
                        if price is not None and price > 0 and pct is not None:
                            prices[code] = pct
                    receipts.append(snapshot.get("received_at"))
                summary["snapshot_count"] = len(prices)
                if not selected:
                    ranked = sorted(prices, key=lambda code: (-prices[code], code))
                    selected = list(dict.fromkeys(ranked[:_EDGE] + ranked[-_EDGE:]))
                summary["candidate_count"] = len(selected)
                summary["missing_snapshot_count"] = len(codes) - len(prices)
                frozen.update(snapshot_changes=prices, snapshot_receipts=receipts, candidates=selected)
                sem = catalog_service._board_fetch_sem

                async def verify(code):
                    try:
                        async with sem:
                            bars = await catalog_service.fetch_board_bars(
                                code, calendar_days=max(8, (day - previous).days + 4))
                        dated = {}
                        for bar in bars:
                            if (not isinstance(bar, dict) or bar.get("source_code") != code
                                    or bar.get("source") != "ths_official_kline"):
                                raise ValueError("日线源身份不符")
                            stamp = bar.get("date")
                            raw_ms = bar.get("source_date_ms")
                            if (type(raw_ms) is not int or raw_ms <= 0
                                    or datetime.fromtimestamp(raw_ms / 1000, tz=BJ_TZ).date().isoformat() != stamp):
                                raise ValueError("日线原始日期标签与交易日身份不符")
                            if stamp in dated:
                                raise ValueError("日线重复日期")
                            close = _number(bar.get("close"))
                            if close is not None and close > 0:
                                dated[stamp] = (close, raw_ms)
                        if day.isoformat() not in dated or previous.isoformat() not in dated:
                            raise ValueError("缺报告日或前交易日的有效官方日线")
                        (close, day_ms), (before, previous_ms) = dated[day.isoformat()], dated[previous.isoformat()]
                        return {"board_code": code, "name": identities[code], "kind": kind,
                                "change_pct": round((close / before - 1) * 100, 4),
                                "close": close, "prev_close": before, "main_net_yi": None,
                                "source": "ths_official_kline", "source_trade_date": day.isoformat(),
                                "source_date_ms": day_ms, "previous_source_date_ms": previous_ms,
                                "previous_trade_date": previous.isoformat(), "source_as_of": None,
                                "received_at": owner.beijing_now().isoformat(),
                                "binding_state": "partial", "time_basis": "官方日线日期标签；日内更新时刻未知，不补写15:00源钟"}
                    except Exception as exc:
                        return {"missing_code": code, "reason": str(exc)[:160]}

                existing = {row["board_code"]: row for row in result["boards"][kind]}
                observations = await asyncio.gather(*(verify(code) for code in selected if code not in existing))
                rows = list(existing.values()) + [row for row in observations if "board_code" in row]
                rows.sort(key=lambda row: (-row["change_pct"], row["board_code"]))
                result["boards"][kind] = rows
                summary["verified_count"] = len(rows)
                summary["missing_count"] = len(selected) - len(rows)
                summary["missing"] = [row for row in observations if "missing_code" in row]
                old_missing = {row["missing_code"]: row for row in prior.get("missing", [])}
                for missing in summary["missing"]:
                    old = old_missing.get(missing["missing_code"])
                    if old:
                        missing["previous_reasons"] = list(dict.fromkeys(
                            old.get("previous_reasons", []) + [old["reason"]]))
                summary["reason"] = "快照仅选候选；官方报告日与前交易日日线核价，强弱为已核样本排序；资金未取得"
            except Exception as exc:
                summary["reason"] = str(exc)[:240]
                if prior.get("reason"):
                    summary["previous_reason"] = prior["reason"]
        count = sum(len(rows) for rows in result["boards"].values())
        result.update(state="partial" if count else "unavailable", received_at=owner.beijing_now().isoformat(),
                      reason="同花顺官方目录的快照候选经日线核价；仅已核强弱样本，不代表全市场最强；资金与日内源钟未取得")
        return _save(day, result, inputs, expected_version)

    return deepcopy(await read_inflight(owner, ("review-board-daily", str(owner._DAILY_STORE.resolve()), day.isoformat()), load))
