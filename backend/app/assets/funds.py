"""Parse documented fund facts without upgrading their dates or missing coverage."""
from __future__ import annotations

import math
import re
from datetime import date, datetime, timezone

from app.core.bjtime import BJ_TZ
from app.core.errors import AppError
from app.schemas.assets import (
    AssetInstrument, FundHolding, FundHoldingsSection, FundNavPoint,
    FundNavSection, FundTerm, FundTermsSection,
)

FUND_CODE = re.compile(r"^[0-9]{6}\.(OF|SH|SZ)$")
FUND_TYPES = {"fund-otc", "fund-etf", "fund-lof", "fund-reits"}
# Explicitly supported settlement currencies. A syntactically valid unknown
# three-letter marker (including ISO XXX = no currency) cannot price a holding.
CURRENCIES = {"CNY", "USD", "HKD", "EUR", "GBP", "JPY", "CHF", "CAD", "AUD", "NZD", "SGD"}


class AssetSourceError(AppError):
    status_code = 502
    code = "asset_source_unavailable"


def fund_code(value: str, *, root: bool = True) -> str:
    code = value.strip().upper()
    if not FUND_CODE.fullmatch(code) or (root and not code.endswith(".OF")):
        raise AppError("请选择来源目录中的场外基金完整代码", code="invalid_fund_identity", status_code=422)
    return code


def instrument(row: dict, *, root: bool = True) -> AssetInstrument:
    code = str(row.get("thscode") or "")
    if not FUND_CODE.fullmatch(code):
        raise AssetSourceError("基金目录身份无效", code="fund_identity_mismatch")
    symbol, market = code.split(".")
    kind, currency = row.get("asset_type"), row.get("currency")
    if kind not in FUND_TYPES or (root and (kind != "fund-otc" or market != "OF")):
        raise AssetSourceError("来源标的不是本轮支持的场外基金", code="fund_type_unsupported")
    if row.get("ticker") != symbol or (row.get("exchange") not in (None, "", market)):
        raise AssetSourceError("基金目录代码与市场不一致", code="fund_identity_mismatch")
    if not isinstance(currency, str) or currency not in CURRENCIES:
        raise AssetSourceError("来源未明确基金份额币种", code="fund_currency_unknown")
    name = row.get("name")
    if not isinstance(name, str) or not name.strip():
        raise AssetSourceError("来源缺少基金名称", code="fund_name_missing")
    return AssetInstrument(
        instrument_id=f"fund:{market}:{symbol}:{currency}", fund_type=kind,
        market=market, symbol=symbol, source_symbol=code, currency=currency,
        name=name.strip()[:160],
    )


def source_time(value) -> datetime | None:
    if isinstance(value, bool):
        raise AssetSourceError("来源日期格式无效", code="fund_date_invalid")
    if value is None or value == 0:
        return None
    if not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AssetSourceError("来源日期格式无效", code="fund_date_invalid")
    try:
        result = datetime.fromtimestamp(value / 1000, timezone.utc)
    except (ValueError, OSError, OverflowError) as exc:
        raise AssetSourceError("来源日期越界", code="fund_date_invalid") from exc
    if result.year < 1990:
        raise AssetSourceError("来源日期不在有效范围", code="fund_date_invalid")
    return result


def source_date(value, today: date) -> date | None:
    stamp = source_time(value)
    if stamp is None:
        return None
    result = stamp.astimezone(BJ_TZ).date()
    if result > today or result.year < 1990:
        raise AssetSourceError("来源日期不在有效范围", code="fund_date_invalid")
    return result


def finite_number(value, *, positive: bool = False, weight: bool = False) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise AssetSourceError("来源数值无效", code="fund_value_invalid")
    result = float(value)
    if positive and result <= 0 or weight and not 0 <= result <= 100:
        raise AssetSourceError("来源数值超出有效范围", code="fund_value_invalid")
    return result


def rows(data: dict, *, max_rows: int) -> list[dict]:
    items = data.get("item")
    if not isinstance(items, list) or len(items) > max_rows or any(not isinstance(row, dict) for row in items):
        raise AssetSourceError("来源数据结构或范围无效", code="fund_shape_invalid")
    return items


def check_parent_identity(data: dict, code: str) -> None:
    # NAV/holdings contracts omit the parent code. Their identity is bound by the
    # verified directory plus request. If returned, a conflicting code is fatal.
    for key in ("thscode", "fund_thscode"):
        if data.get(key) is not None and data[key] != code:
            raise AssetSourceError("来源返回了其他基金", code="fund_identity_mismatch")


def parse_nav(data: dict, code: str, currency: str, today: date, evidence) -> FundNavSection:
    check_parent_identity(data, code)
    values, dates = [], set()
    for row in rows(data, max_rows=400):
        check_parent_identity(row, code)
        for returned_currency in (data.get("currency"), row.get("currency")):
            if returned_currency is not None and returned_currency != currency:
                raise AssetSourceError("净值币种与基金份额不一致", code="fund_currency_mismatch")
        day = source_date(row.get("nav_date"), today)
        if day is None or day in dates:
            raise AssetSourceError("净值日期缺失或重复", code="fund_date_invalid")
        dates.add(day)
        unit = finite_number(row.get("unit_nav"), positive=True)
        adj = finite_number(row.get("adj_nav"), positive=True)
        if unit is None and adj is None:
            raise AssetSourceError("来源净值为空", code="fund_value_invalid")
        values.append(FundNavPoint(nav_date=day, unit_nav=unit, adj_nav=adj))
    values.sort(key=lambda row: row.nav_date)
    missing = ["publication_time"]
    if not values:
        return FundNavSection(state="unavailable", reason="来源尚无净值", missing=["nav"], currency=currency, evidence=[evidence])
    if values[-1].unit_nav is None:
        missing.append("unit_nav")
    if values[-1].adj_nav is None:
        missing.append("adjusted_nav")
    return FundNavSection(state="partial", reason="按估值日期展示；公布时刻未提供", missing=missing,
                          currency=currency, points=values, latest=values[-1], evidence=[evidence])


def parse_holdings(data: dict, code: str, today: date, evidence) -> FundHoldingsSection:
    check_parent_identity(data, code)
    values, identities, report_dates, missing = [], set(), set(), set()
    for row in rows(data, max_rows=200):
        identity = row.get("thscode")
        kind = row.get("asset_type")
        if not isinstance(identity, str) or not re.fullmatch(r"[A-Z0-9._-]{1,40}", identity):
            raise AssetSourceError("持仓证券身份缺失", code="fund_identity_mismatch")
        if not isinstance(kind, str) or not kind:
            raise AssetSourceError("持仓资产类别缺失", code="fund_identity_mismatch")
        if identity in identities:
            raise AssetSourceError("持仓证券重复", code="fund_duplicate_holding")
        identities.add(identity)
        day = source_date(row.get("end_date_ms"), today)
        published = source_time(row.get("publish_date_ms"))
        modified = source_time(row.get("modify_time_ms"))
        for stamp in (published, modified):
            if stamp is not None and (stamp > evidence.received_at or stamp.astimezone(BJ_TZ).date() > today
                                      or day is not None and stamp.astimezone(BJ_TZ).date() < day):
                raise AssetSourceError("持仓披露日期与报告期不一致", code="fund_date_invalid")
        if day is not None:
            report_dates.add(day)
        else:
            missing.add("report_date")
        if published is None:
            missing.add("publication_time")
        weight = finite_number(row.get("hold_ratio"), weight=True)
        if weight is None:
            missing.add("holding_weight")
        values.append(FundHolding(source_symbol=identity, name=str(row.get("stock_name") or identity)[:160],
                                  asset_type=kind, weight_pct=weight, report_date=day,
                                  published_at=published, modified_at=modified))
    if len(report_dates) > 1:
        raise AssetSourceError("持仓混用了不同报告期", code="fund_report_mismatch")
    coverage = sum(row.weight_pct or 0 for row in values)
    if coverage > 100.000001:
        raise AssetSourceError("披露权重超过净值，暂不穿透", code="fund_weight_unsupported")
    if coverage < 100:
        missing.add("undisclosed_positions")
    return FundHoldingsSection(
        state="partial" if values else "unavailable", reason="定期披露持仓，不代表当前仓位" if values else "来源尚无披露持仓",
        missing=sorted(missing or {"current_positions"}), evidence=[evidence], items=values,
        disclosed_weight_pct=round(coverage, 6) if values else None,
        report_date=next(iter(report_dates)) if report_dates else None,
    )


def parse_terms(data: dict, code: str, evidence) -> FundTermsSection:
    check_parent_identity(data, code)
    items = rows(data, max_rows=1)
    if len(items) != 1 or items[0].get("thscode") != code:
        raise AssetSourceError("基金资料身份不一致", code="fund_identity_mismatch")
    row = items[0]
    fees, dealing = [], []
    for source, target, label_key, value_key in (
        (row.get("rate_info", []), fees, "rate_type", "standard_rate"),
        (row.get("trade_rule", []), dealing, "title", "display_time"),
    ):
        if not isinstance(source, list) or len(source) > 40:
            raise AssetSourceError("基金费率或交易规则结构无效", code="fund_shape_invalid")
        for item in source:
            if not isinstance(item, dict):
                raise AssetSourceError("基金规则行无效", code="fund_shape_invalid")
            label, value = item.get(label_key), item.get(value_key)
            if isinstance(label, str) and isinstance(value, str) and label.strip() and value.strip():
                condition = item.get("condition")
                target.append(FundTerm(label=label[:100], value=value[:500],
                                       condition=condition[:500] if isinstance(condition, str) else None))
    # Published profile, portfolio and diagnostics contracts do not define an
    # investment mandate or benchmark. A name/diagnostic probability is not one.
    missing = ["investment_scope", "benchmark", "subscription_status"]
    if not fees:
        missing.append("fees")
    if not dealing:
        missing.append("dealing_rules")
    manager = row.get("mgmt_name")
    return FundTermsSection(state="partial", reason="费率及规则按来源原文；投资范围与申购状态待核",
                            missing=missing, evidence=[evidence], fees=fees, dealing_rules=dealing,
                            manager_name=manager[:160] if isinstance(manager, str) else None)
