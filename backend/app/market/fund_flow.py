"""两市成交额对比 + 资金流向净额（市场页「资金」Tab 底座）。

## 数据源选型（2026-09-04 实测，文档 skills/hithink-finance/docs/api/capability-map.md）

按用户要求先验证 ths：59 个官方端点**确认无**资金流向、无指数成交额字段
（capability-map 全量核对），故按「无则选其他可靠源」降级为以下组合：

| 数据 | 源 | 实测结论 |
|---|---|---|
| 今日实时成交额（沪/深合计） | 腾讯快照 sh000001 + sz399107（Quote.amount） | 稳定 |
| 今日分时累计曲线 | 腾讯 minute/query（cum_amount 每分钟累计） | 稳定 |
| 昨日/历史同时刻累计、历史全日 | 新浪 5 分钟K（amount 元，~12 交易日回溯） | 稳定 |
| 实时资金五档净额 | 东财 push2 ulist（大盘口径=该市场全部股票合计） | ~1/3 概率空响应，重试 2 次 |
| 历史日度资金流 | 东财 push2his fflow daykline | 间歇拦截 → 拉取后本地落盘 |

## 口径（红线：不虚构）

- 成交额 = **沪深两市**（北交所无资金流数据，不混入口径；市场总览旧卡是
  「沪深京」全市场快照口径，两处各自标注，不互相冒充）。
- 资金分档 = 东财真实口径：超大单/大单/中单/小单（主力=超大+大）。
  **机构/游资在实时全市场数据源中不存在拆分**（仅龙虎榜日度有），绝不臆造。
- 自洽校验（2026-09-04 实测）：主力+中单+小单 ≈ 0（有买必有卖），误差约亿级以内。
- 三态纪律：昨日同时刻/历史数据缺失 → None + degraded 说明，绝不填 0 或猜测。

## 全日估算

优先按「昨日同时刻累计占昨日全日比例」外推（A 股成交量分布 U 型，比线性准）；
昨日分布缺失时退线性进度；收盘后返回实际值并标注 est_method。
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from app.core.ttl_cache import TTLCache

log = logging.getLogger(__name__)

SH_SYMBOL = "sh000001"  # 上证指数 = 沪市全市场
SZ_SYMBOL = "sz399107"  # 深证A指 = 深市全市场（监管偏离同口径）
YI = 1e8
_TZ_BJ = timezone(timedelta(hours=8))

#: 历史资金流落盘（绕开东财 push2his 间歇拦截：拉一次存本地，读路径零外呼）
_FLOW_STORE = Path(__file__).resolve().parents[2] / "data" / "fundflow" / "daily.json"

#: 新浪 5 分钟K缓存：历史 bar 不可变，5min 足够；失败/None 不缓存
_SINA_CACHE = TTLCache("sina-mk-bars", ttl=300.0, maxsize=8)
#: 今日对比缓存（盘中 30s）
_TURNOVER_CACHE = TTLCache("turnover-today", ttl=30.0, maxsize=2)
#: 实时资金流缓存（30s；**失败不缓存**——东财间歇空响应靠下一拍自愈）
_FLOW_RT_CACHE = TTLCache("fundflow-rt", ttl=30.0, maxsize=1)
#: 历史资金流内存缓存（6h；真正增量在落盘文件）
_FLOW_HIST_CACHE = TTLCache("fundflow-hist", ttl=6 * 3600.0, maxsize=1)

_HTTP = None

# 技术债豁免（2026-09-07 健康度审查 O3/P1-5，显式化而非隐瞒）：
# 本模块自建 httpx + 手写 range(3) 重试，不走 provider 链的统一熔断
# （composite._cooldown_until）。理由：资金流是大盘/板块级低频读（分钟/日度），
# 与个股行情链路熔断域隔离，避免单个接口抖动熔断拖累行情主链；代价是
# 「无统一熔断观测」，若东财长时间故障仅靠 log+缓存降级可见。
# 收敛为 provider 链调用的方案见 docs/system-health-review-20260907.html P1-5。
def _http():
    global _HTTP
    if _HTTP is None:
        import httpx

        _HTTP = httpx.AsyncClient(
            trust_env=False,  # 国内行情源直连，不走系统代理
            timeout=8.0,
            # Connection: close：东财 push2/push2his 对 keep-alive 复用极不友好
            # （Server disconnected without sending a response，2026-09-04 实测），
            # 短连接后与 curl 行为一致。
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/",
                     "Connection": "close"},
        )
    return _HTTP


# ---------------------------------------------------------------- 时间/进度工具

def _now_bj() -> datetime:
    return datetime.now(_TZ_BJ)


def _market_progress_minutes(now: datetime) -> int | None:
    """开盘以来分钟数（连续竞价口径）：09:30=0 … 11:30/13:00=120 … 15:00=240。

    非交易时段返回 None（盘前/周末/节假日——节假日由调用方结合日历判定）。
    """
    t = now.time()
    if time(9, 30) <= t <= time(11, 30):
        return int((t.hour * 60 + t.minute) - (9 * 60 + 30))
    if time(13, 0) <= t <= time(15, 0):
        return 120 + int((t.hour * 60 + t.minute) - 13 * 60)
    if t > time(15, 0):
        return 240
    return None  # 盘前/午休前半段之外不估算


def _sina_bar_seq(dt: datetime) -> int | None:
    """新浪 5 分钟K bar 的交易分钟序号（按 bar 结束时刻口径）。非交易时段标签 → None。"""
    t = dt.time()
    hm = t.hour * 60 + t.minute
    if hm == 9 * 60 + 30:  # 09:30 集合竞价 bar
        return 0
    if 9 * 60 + 30 < hm <= 11 * 60 + 30:
        return hm - (9 * 60 + 30)
    if 13 * 60 < hm <= 15 * 60:
        return 120 + (hm - 13 * 60)
    return None


# ---------------------------------------------------------------- 新浪 5 分钟K

async def _sina_mk_bars(symbol: str, datalen: int = 600) -> list[dict] | None:
    """新浪 5 分钟K → [{dt, seq, amount}]（amount 元/根）。失败返回 None 不缓存。"""
    key = (symbol, datalen)
    hit, cached = _SINA_CACHE.get(key)
    if hit:
        return cached
    try:
        resp = await _http().get(
            "https://quotes.sina.cn/cn/api/json_v2.php/CN_MarketDataService.getKLineData",
            params={"symbol": symbol, "scale": 5, "ma": "no", "datalen": datalen},
        )
        resp.raise_for_status()
        rows = resp.json()
        if not isinstance(rows, list) or not rows:
            raise ValueError("empty bars")
        bars: list[dict] = []
        for r in rows:
            dt = datetime.strptime(r["day"], "%Y-%m-%d %H:%M:%S")
            seq = _sina_bar_seq(dt)
            if seq is None:
                continue
            amt = r.get("amount")
            if amt in (None, ""):
                continue
            bars.append({"dt": dt, "seq": seq, "amount": float(amt)})
        if not bars:
            raise ValueError("no valid bars")
    except Exception as exc:  # noqa: BLE001
        log.warning("sina 5m bars %s failed: %s", symbol, exc)
        return None
    _SINA_CACHE.set(key, bars)
    return bars


def _cum_amount_by_seq(bars: list[dict], day: date, max_seq: int | None = None) -> float | None:
    """指定交易日的累计成交额（元）；max_seq=None 算全日。当日无 bar → None（三态）。"""
    total = 0.0
    seen = False
    for b in bars:
        if b["dt"].date() != day:
            continue
        seen = True
        if max_seq is None or b["seq"] <= max_seq:
            total += b["amount"]
    return total if seen else None


# ---------------------------------------------------------------- 今日成交额对比

async def get_turnover_today(hub) -> dict:
    """今日实时两市成交额 + 昨日同一时刻对比 + 全日估算 + 两日分时累计曲线。

    所有子环节失败都显式降级（degraded 列表），绝不臆造；缓存 30s，失败不缓存。
    """
    hit, cached = _TURNOVER_CACHE.get("today")
    if hit:
        return cached

    degraded: list[str] = []
    out = await _turnover_today_uncached(hub, degraded)
    # 失败不缓存（腾讯/新浪瞬时故障下一拍重试）；成功才缓存
    if not out.get("degraded") or out.get("today_amount") is not None:
        _TURNOVER_CACHE.set("today", out)
    return out


async def _turnover_today_uncached(hub, degraded: list[str]) -> dict:
    from app.market.trade_calendar import prev_trade_date, trading_days

    tp = _tencent_provider(hub)
    if tp is None:
        degraded.append("腾讯源不可用")

    # --- 今日实时合计（快照 amount，元）---
    today_amount: float | None = None
    if tp is not None:
        try:
            quotes = await tp.get_quotes([SH_SYMBOL, SZ_SYMBOL])
            amts = [q.amount for q in quotes]
            if all(a is not None for a in amts):
                today_amount = float(sum(amts))
            else:
                degraded.append("快照成交额字段缺失")
        except Exception as exc:  # noqa: BLE001
            degraded.append(f"实时快照不可用({type(exc).__name__})")

    # --- 今日分时累计曲线（两市按分钟相加）---
    today_series: list[dict] | None = None
    if tp is not None:
        try:
            sh_pts, sz_pts = await asyncio.gather(
                tp.get_minute_line(SH_SYMBOL), tp.get_minute_line(SZ_SYMBOL)
            )
            today_series = _merge_cum_series(sh_pts, sz_pts)
        except Exception as exc:  # noqa: BLE001
            degraded.append(f"今日分时不可用({type(exc).__name__})")

    # --- 昨日数据（新浪 5 分钟K）---
    prev_total: float | None = None
    prev_same: float | None = None
    prev_series: list[dict] | None = None
    prev_date: str | None = None
    sina_ok = False
    try:
        days = await trading_days(_calendar_provider(hub))
    except Exception as exc:  # noqa: BLE001
        days = []
        degraded.append(f"交易日历不可用({type(exc).__name__})")
    if days:
        pd_ = prev_trade_date(days, _now_bj().date())
        if pd_ is not None:
            prev_date = pd_.isoformat()
            sh_bars, sz_bars = await asyncio.gather(
                _sina_mk_bars(SH_SYMBOL), _sina_mk_bars(SZ_SYMBOL)
            )
            if sh_bars is not None and sz_bars is not None:
                sina_ok = True
                prev_total_sh = _cum_amount_by_seq(sh_bars, pd_)
                prev_total_sz = _cum_amount_by_seq(sz_bars, pd_)
                if prev_total_sh is not None and prev_total_sz is not None:
                    prev_total = prev_total_sh + prev_total_sz
                else:
                    degraded.append("昨日全日成交额缺失")
                progress = _market_progress_minutes(_now_bj())
                if progress is not None:
                    ps_sh = _cum_amount_by_seq(sh_bars, pd_, max_seq=progress)
                    ps_sz = _cum_amount_by_seq(sz_bars, pd_, max_seq=progress)
                    if ps_sh is not None and ps_sz is not None:
                        prev_same = ps_sh + ps_sz
                prev_series = _sina_day_series(sh_bars, sz_bars, pd_)
            else:
                degraded.append("新浪分钟K不可用，昨日对比缺失")

    # --- 全日估算 ---
    est_full_day = None
    est_method = None
    now = _now_bj()
    progress = _market_progress_minutes(now)
    if today_amount is not None:
        if progress is not None and progress >= 240:
            est_full_day, est_method = today_amount, "closed"
        elif prev_same is not None and prev_total not in (None, 0):
            est_full_day = today_amount / (prev_same / prev_total)
            est_method = "prev-dist"
        elif progress and progress > 0:
            est_full_day = today_amount / (progress / 240)
            est_method = "linear"
        else:
            est_full_day, est_method = None, None  # 盘前：无估算（不臆造）
    if est_method is None and progress is not None and progress < 240:
        degraded.append("全日估算不可用（缺昨日分布）")

    return {
        "today_amount_yi": _yi(today_amount),
        "prev_date": prev_date,
        "prev_same_time_yi": _yi(prev_same),
        "prev_total_yi": _yi(prev_total),
        "diff_yi": (_yi(today_amount) - _yi(prev_same))
        if today_amount is not None and prev_same is not None
        else None,
        "est_full_day_yi": _yi(est_full_day),
        "est_method": est_method,
        "today_series": today_series,   # [{t:"HH:MM", cum: 亿元}]
        "prev_series": prev_series,     # 同结构（昨日）
        "sina_available": sina_ok,
        "updated_at": now.strftime("%H:%M:%S"),
        "degraded": degraded,
    }


def _tencent_provider(hub):
    """从 provider 链里找 TencentProvider 实例（get_quotes/get_minute_line 用）。"""
    p = getattr(hub, "provider", None)
    chain = getattr(p, "providers", None) if p is not None else None
    for cand in (chain or [p]):
        if cand is not None and getattr(cand, "name", "") == "tencent":
            return cand
    return None


def _calendar_provider(hub):
    return getattr(hub, "provider", None)


def _yi(v: float | None) -> float | None:
    return round(v / YI, 2) if v is not None else None


def _minute_label(ts) -> str:
    """分时点 ts（UTC ISO 串或 datetime）→ 北京时间 HH:MM。"""
    if isinstance(ts, str):
        dt = datetime.fromisoformat(ts).astimezone(_TZ_BJ)
    else:
        dt = ts.astimezone(_TZ_BJ)
    return dt.strftime("%H:%M")


def _merge_cum_series(sh_pts: list[dict], sz_pts: list[dict]) -> list[dict]:
    """两市分时累计成交额按分钟相加 → [{t, cum}]（亿元，升序）。"""
    sz_by_t = {_minute_label(p["ts"]): p for p in sz_pts}
    out: list[dict] = []
    for p in sh_pts:
        t = _minute_label(p["ts"])
        ca_sh = p.get("cum_amount")
        ca_sz = (sz_by_t.get(t) or {}).get("cum_amount")
        if ca_sh is None or ca_sz is None:
            continue
        out.append({"t": t, "cum": round((ca_sh + ca_sz) / YI, 2)})
    return out


def _sina_day_series(sh_bars: list[dict], sz_bars: list[dict], day: date) -> list[dict] | None:
    """新浪两市场 bar 按 seq 对齐相加 → 当日累计曲线 [{t, cum}]（亿元）。"""
    sz_by_seq = {b["seq"]: b for b in sz_bars if b["dt"].date() == day}
    out: list[dict] = []
    cum = 0.0
    for b in sorted((b for b in sh_bars if b["dt"].date() == day), key=lambda x: x["seq"]):
        pair = sz_by_seq.get(b["seq"])
        if pair is None:
            continue
        cum += b["amount"] + pair["amount"]
        out.append({"t": b["dt"].strftime("%H:%M"), "cum": round(cum / YI, 2)})
    return out or None


# ---------------------------------------------------------------- 实时资金流

_EM_FIELDS = ("f12", "f14", "f62", "f66", "f72", "f78", "f84", "f184", "f124")
#: f62 主力 / f66 超大单 / f72 大单 / f78 中单 / f84 小单（元）；f124 更新时间戳
_FLOW_KEYS = {"f62": "main", "f66": "super_", "f72": "big", "f78": "mid", "f84": "small"}
#: fflow kline 分钟线字段序：时间,主力,小单,中单,大单,超大单（累计净额，元）
_FFLOW_MIN_KEYS = ("main", "small", "mid", "big", "super_")

_INTRADAY_CACHE = TTLCache("fundflow-intraday", ttl=60.0, maxsize=1)


async def get_fund_flow_intraday() -> dict:
    """今日**分钟级**资金流累计曲线（沪深合计，亿元）——像分时图一样记录各档资金。

    数据源：东财 push2delay fflow kline klt=1（延迟约 15 分钟的免费口径，
    图表必须标注）。沪深各拉一次按分钟对齐相加；缓存 60s；失败显式降级。
    历史日的分钟资金流东财不提供（仅当日）——历史回看降级为日度五档+分钟成交额对比。
    """
    hit, cached = _INTRADAY_CACHE.get("intraday")
    if hit:
        return cached
    degraded: list[str] = []
    series: dict[str, dict] = {}
    for i, (mkt, secid) in enumerate((("sh", "1.000001"), ("sz", "0.399107"))):
        rows = await _em_fflow_kline(secid)
        if rows is None:
            degraded.append(f"{mkt} 分钟资金流不可用")
            continue
        for t, vals in rows:
            node = series.setdefault(t, {})
            node[mkt] = vals
        if i == 0:
            await asyncio.sleep(1.5)  # 防熔断
    items: list[dict] = []
    if len(degraded) == 0:
        for t in sorted(series):
            sh, sz = series[t].get("sh"), series[t].get("sz")
            if sh is None or sz is None:
                continue
            item = {"t": t}
            for k in _FFLOW_MIN_KEYS:
                a, b = sh.get(k), sz.get(k)
                item[k] = round(a + b, 2) if (a is not None and b is not None) else None
            items.append(item)
    out = {
        "items": items,  # [{t, main, super_, big, mid, small}]（亿元，累计口径）
        "updated_at": _now_bj().strftime("%H:%M:%S"),
        "degraded": degraded,
    }
    if not out["degraded"]:
        _INTRADAY_CACHE.set("intraday", out)  # 失败不缓存
    return out


async def _em_fflow_kline(secid: str) -> list[tuple[str, dict]] | None:
    """东财 fflow kline（分钟，累计净额元）→ [(“HH:MM”, {key: 亿元})]。重试 2 次。"""
    for attempt in range(3):
        try:
            resp = await _http().get(
                "https://push2delay.eastmoney.com/api/qt/stock/fflow/kline/get",
                params={
                    "lmt": 0, "klt": 1, "secid": secid,
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56",
                },
                headers={"Referer": "https://quote.eastmoney.com/"},
            )
            resp.raise_for_status()
            klines = ((resp.json().get("data") or {}).get("klines") or [])
            if not klines:
                raise ValueError("empty klines")
            out: list[tuple[str, dict]] = []
            for line in klines:
                parts = line.split(",")
                if len(parts) < 6:
                    continue
                hm = parts[0].split(" ")[1][:5] if " " in parts[0] else parts[0]
                vals = {k: _em_num_raw(parts[i + 1]) for i, k in enumerate(_FFLOW_MIN_KEYS)}
                out.append((hm, vals))
            return out
        except Exception as exc:  # noqa: BLE001
            log.warning("em fflow kline %s attempt %s failed: %s", secid, attempt + 1, exc)
            await asyncio.sleep(1.0)
    return None


def _em_num_raw(v) -> float | None:
    if v in (None, "-", ""):
        return None
    try:
        return float(v) / YI
    except (TypeError, ValueError):
        return None


async def get_fund_flow_realtime() -> dict:
    """东财大盘资金流（沪深两市场合计）：五档净额（亿元）。

    东财间歇空响应 → 重试 2 次；全部失败显式 available=False（绝不填 0）。
    """
    hit, cached = _FLOW_RT_CACHE.get("rt")
    if hit:
        return cached
    out = await _fund_flow_rt_uncached()
    if out.get("available"):
        _FLOW_RT_CACHE.set("rt", out)  # 失败不缓存
        _snapshot_today_if_closed(out)
    return out


def _snapshot_today_if_closed(rt: dict) -> None:
    """收盘后（>=15:05）首次拿到成功实时数据时，把当日五档净额快照进日度库。

    这样即使东财 daykline 回源长期不可达，历史序列也能从现在起逐日自然积累
    （当日累计净额收盘定格 = 日度 bar，口径一致）。
    """
    try:
        today = _now_bj()
        if today.hour < 15 or (today.hour == 15 and today.minute < 5):
            return
        store = _read_flow_store() or {"updated_at": None, "days": {}}
        d = today.date().isoformat()
        if d in (store.get("days") or {}):
            return
        items = rt.get("items") or {}
        sh, sz = items.get("sh"), items.get("sz")
        if not sh or not sz:
            return
        store["days"][d] = {"sh": sh, "sz": sz, "close_pct": None, "source": "snapshot"}
        store["updated_at"] = today.isoformat(timespec="seconds")
        _FLOW_STORE.parent.mkdir(parents=True, exist_ok=True)
        _FLOW_STORE.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
        _FLOW_HIST_CACHE.invalidate()
        log.info("fund-flow daily snapshot saved for %s", d)
    except Exception:  # noqa: BLE001  快照失败不影响实时返回
        log.warning("fund-flow daily snapshot failed", exc_info=True)


async def _fund_flow_rt_uncached() -> dict:
    degraded: list[str] = []
    diff = await _em_ulist()
    if diff is not None:
        return _rt_from_ulist(diff, degraded)
    # ulist 全败（东财逐连接抖动）：回退 push2delay 分钟流末根 bar——同为累计五档净额，
    # 口径一致但延迟约 15 分钟，显式标注绝不冒充实时。
    fallback = await _rt_from_intraday_tail()
    if fallback is not None:
        fallback["degraded"] = degraded + ["实时源不可用，采用 15 分钟延迟口径"]
        return fallback
    return {"available": False, "reason": "东财资金流源不可用（已重试）",
            "degraded": ["资金流实时数据不可用"]}


def _rt_from_ulist(diff: list[dict], degraded: list[str]) -> dict:
    by_mkt: dict[str, dict] = {}
    for row in diff:
        code = str(row.get("f12", ""))
        mkt = "sh" if code == "000001" else ("sz" if code == "399107" else None)
        if mkt is None:
            continue
        by_mkt[mkt] = {k: _em_num(row.get(f)) for f, k in _FLOW_KEYS.items()}
    if "sh" not in by_mkt or "sz" not in by_mkt:
        return {"available": False, "reason": "资金流响应缺沪深任一市场", "degraded": ["资金流实时数据不完整"]}
    total = {k: _sum3(by_mkt["sh"].get(k), by_mkt["sz"].get(k)) for k in _FLOW_KEYS.values()}
    ts = next((r.get("f124") for r in diff if r.get("f124")), None)
    as_of = datetime.fromtimestamp(ts, _TZ_BJ).strftime("%H:%M:%S") if ts else None
    return {
        "available": True,
        "as_of": as_of,
        # 亿元；self-check：main+mid+small ≈ 0（买卖对冲），前端可展示校验提示
        "items": {"total": total, "sh": by_mkt["sh"], "sz": by_mkt["sz"]},
        "degraded": degraded,
    }


async def _rt_from_intraday_tail() -> dict | None:
    """push2delay 分钟资金流末根 bar → 实时五档净额（延迟口径）。"""
    sh, sz = await asyncio.gather(
        _em_fflow_kline("1.000001"), _em_fflow_kline("0.399107"))
    if not sh or not sz:
        return None
    tail_sh, tail_sz = sh[-1], sz[-1]
    if tail_sh[0] != tail_sz[0]:  # 两市末根分钟不一致时取较早者，保证合计口径同刻
        idx_sh = next((i for i, p in enumerate(sh) if p[0] == tail_sz[0]), None)
        idx_sz = next((i for i, p in enumerate(sz) if p[0] == tail_sh[0]), None)
        if idx_sh is None and idx_sz is None:
            return None
        if idx_sh is not None:
            tail_sh = sh[idx_sh]
        else:
            tail_sz = sz[idx_sz]
    total = {k: _sum3(tail_sh[1].get(k), tail_sz[1].get(k)) for k in _FLOW_KEYS.values()}
    return {"available": True, "as_of": tail_sh[0] + " (延迟)",
            "items": {"total": total,
                      "sh": dict(tail_sh[1]), "sz": dict(tail_sz[1])}}


def _em_num(v) -> float | None:
    if v in (None, "-", ""):
        return None
    try:
        return round(float(v) / YI, 2)
    except (TypeError, ValueError):
        return None


def _sum3(a: float | None, b: float | None) -> float | None:
    return round(a + b, 2) if a is not None and b is not None else None


async def _em_ulist() -> list[dict] | None:
    """东财 push2 ulist 大盘资金流，空响应/异常重试 2 次（实测 ~1/3 概率空响应）。"""
    for attempt in range(3):
        try:
            resp = await _http().get(
                "https://push2.eastmoney.com/api/qt/ulist.np/get",
                params={"fltt": 2, "secids": "1.000001,0.399107", "fields": ",".join(_EM_FIELDS)},
                headers={"Referer": "https://quote.eastmoney.com/"},
            )
            if resp.status_code != 200:
                raise ValueError(f"HTTP {resp.status_code}")
            diff = ((resp.json().get("data") or {}).get("diff") or [])
            if len(diff) >= 2:
                return diff
            raise ValueError("diff < 2")
        except Exception as exc:  # noqa: BLE001
            log.warning("em fund-flow ulist attempt %s failed: %s", attempt + 1, exc)
            await asyncio.sleep(1.0)
    return None


# ---------------------------------------------------------------- 历史资金流（落盘）

_BACKFILL_MIN_INTERVAL_S = 300
_last_backfill_ts: list[float] = [0.0]


def _backfill_throttled() -> bool:
    """回源失败时 5 分钟内不再重试，避免东财断连期被历史请求打爆。"""
    import time

    now = time.monotonic()
    if now - _last_backfill_ts[0] < _BACKFILL_MIN_INTERVAL_S:
        return True
    _last_backfill_ts[0] = now
    return False

async def get_fund_flow_history(hub, days: int = 30) -> dict:
    """日度资金流序列（沪深合计，亿元），由近及远。

    读本地 daily.json（读路径零外呼）；文件落后（缺最近完整交易日）时拉东财
    fflow daykline（lmt=40）合并落盘。今日进行中的数据不落盘（用实时端点）。
    """
    hit, cached = _FLOW_HIST_CACHE.get(("hist", days))
    if hit:
        return cached
    store = _read_flow_store()
    store_days = set((store or {}).get("days") or {})
    degraded: list[str] = []
    refreshed = False
    prev_d: date | None = None
    try:
        from app.market.trade_calendar import last_trade_date, prev_trade_date, trading_days

        cal = await trading_days(_calendar_provider(hub))
        today = _now_bj().date()
        lt = last_trade_date(cal, today)
        if lt is not None and lt < today:
            prev_d = lt  # 今日非交易日或未收盘：最近完整交易日=最近交易日
        else:
            prev_d = prev_trade_date(cal, today) if lt is not None else None
    except Exception as exc:  # noqa: BLE001
        degraded.append(f"交易日历不可用({type(exc).__name__})")

    if prev_d is not None and prev_d.isoformat() not in store_days:
        if _backfill_throttled():
            degraded.append("东财历史回源节流中（本地库待积累）")
        else:
            pulled = await _pull_em_fflow_into_store()
            if pulled:
                store = _read_flow_store()
                store_days = set((store or {}).get("days") or {})
            else:
                degraded.append("东财历史资金流拉取失败（使用本地已有）")
            refreshed = True

    rows: list[dict] = []
    for d in sorted(((store or {}).get("days") or {}), reverse=True):
        rec = store["days"][d]
        rows.append({"date": d, **_flow_total_yi(rec)})
    out = {"items": rows[:days], "updated_at": (store or {}).get("updated_at"),
           "refreshed": refreshed, "degraded": degraded}
    _FLOW_HIST_CACHE.set(("hist", days), out)
    return out


def _read_flow_store() -> dict | None:
    try:
        return json.loads(_FLOW_STORE.read_text(encoding="utf-8"))
    except Exception:
        return None


def _flow_total_yi(rec: dict) -> dict:
    sh, sz = rec.get("sh") or {}, rec.get("sz") or {}
    out = {}
    for k in _FLOW_KEYS.values():
        a, b = sh.get(k), sz.get(k)
        out[k] = _sum3(a, b)
    out["close_pct"] = rec.get("close_pct")
    return out


async def _pull_em_fflow_into_store() -> bool:
    """拉东财 fflow daykline（沪深各一次，sleep 防熔断）合并落盘（跳过今日进行中 bar）。"""
    store = _read_flow_store() or {"updated_at": None, "days": {}}
    ok = False
    for i, (mkt, secid) in enumerate((("sh", "1.000001"), ("sz", "0.399107"))):
        try:
            resp = await _http().get(
                "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
                params={
                    "lmt": 40, "klt": 101, "secid": secid,
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
                },
                headers={"Referer": "https://quote.eastmoney.com/"},
            )
            resp.raise_for_status()
            klines = ((resp.json().get("data") or {}).get("klines") or [])
            for line in klines:
                parts = line.split(",")
                if len(parts) < 13:
                    continue
                d = parts[0]
                if d == _now_bj().date().isoformat():
                    continue  # 今日进行中数据不落盘
                rec = store["days"].setdefault(d, {})
                rec[mkt] = {
                    "main": round(float(parts[1]) / YI, 2),
                    "small": round(float(parts[2]) / YI, 2),
                    "mid": round(float(parts[3]) / YI, 2),
                    "big": round(float(parts[4]) / YI, 2),
                    "super_": round(float(parts[5]) / YI, 2),
                }
                rec["close_pct"] = float(parts[12]) if parts[12] not in ("", "-") else None
            ok = True
        except Exception as exc:  # noqa: BLE001
            log.warning("em fflow daykline %s failed: %s", secid, exc)
        if i == 0:
            await asyncio.sleep(2.0)  # 防熔断
    if ok:
        try:
            _FLOW_STORE.parent.mkdir(parents=True, exist_ok=True)
            store["updated_at"] = _now_bj().isoformat(timespec="seconds")
            _FLOW_STORE.write_text(json.dumps(store, ensure_ascii=False), encoding="utf-8")
            _FLOW_HIST_CACHE.invalidate()
        except Exception:
            log.warning("fundflow store persist failed", exc_info=True)
    return ok


# ---------------------------------------------------------------- 历史成交额对比

async def get_turnover_day(hub, day: date) -> dict:
    """指定历史交易日 vs 前一交易日：全日成交额增减 + 两日分时累计曲线对比。"""
    from app.market.trade_calendar import is_trade_day, prev_trade_date, trading_days

    cal = await trading_days(_calendar_provider(hub))
    if not is_trade_day(cal, day):
        return {"available": False, "reason": f"{day.isoformat()} 非交易日", "degraded": []}
    pd_ = prev_trade_date(cal, day)
    sh_bars, sz_bars = await asyncio.gather(_sina_mk_bars(SH_SYMBOL), _sina_mk_bars(SZ_SYMBOL))
    if sh_bars is None or sz_bars is None:
        return {"available": False, "reason": "新浪分钟K不可用", "degraded": ["历史成交额数据不可用"]}
    degraded: list[str] = []
    total_d = _pair_total(sh_bars, sz_bars, day)
    total_p = _pair_total(sh_bars, sz_bars, pd_) if pd_ else None
    if total_d is None:
        return {"available": False, "reason": f"{day.isoformat()} 超出分钟K覆盖范围（~12 交易日）", "degraded": []}
    if pd_ is not None and total_p is None:
        degraded.append("前一交易日超出分钟K覆盖")
    return {
        "available": True,
        "date": day.isoformat(),
        "prev_date": pd_.isoformat() if pd_ else None,
        "total_yi": total_d,
        "prev_total_yi": total_p,
        "diff_yi": round(total_d - total_p, 2) if (total_p is not None) else None,
        "series": _sina_day_series(sh_bars, sz_bars, day),
        "prev_series": _sina_day_series(sh_bars, sz_bars, pd_) if pd_ else None,
        "degraded": degraded,
    }


def _pair_total(sh_bars: list[dict], sz_bars: list[dict], day: date) -> float | None:
    a = _cum_amount_by_seq(sh_bars, day)
    b = _cum_amount_by_seq(sz_bars, day)
    if a is None or b is None:
        return None
    return round((a + b) / YI, 2)


async def get_turnover_history(hub, days: int = 10) -> dict:
    """近 N 个交易日全日成交额 + vs 前一交易日增减（由近及远）。"""
    from app.market.trade_calendar import last_trade_date, recent_trade_dates, trading_days

    cal = await trading_days(_calendar_provider(hub))
    lt = last_trade_date(cal, _now_bj().date())
    anchor = lt or _now_bj().date()
    ds = recent_trade_dates(cal, anchor, days + 1)  # 多取一天作对比基数
    sh_bars, sz_bars = await asyncio.gather(_sina_mk_bars(SH_SYMBOL), _sina_mk_bars(SZ_SYMBOL))
    if sh_bars is None or sz_bars is None:
        return {"items": [], "degraded": ["新浪分钟K不可用，历史成交额不可用"]}
    items: list[dict] = []
    for i, d in enumerate(ds):
        total = _pair_total(sh_bars, sz_bars, d)
        prev_total = _pair_total(sh_bars, sz_bars, ds[i + 1]) if i + 1 < len(ds) else None
        items.append({
            "date": d.isoformat(),
            "total_yi": total,
            "prev_total_yi": prev_total,
            "diff_yi": round(total - prev_total, 2) if (total is not None and prev_total is not None) else None,
        })
    return {"items": [it for it in items if it["total_yi"] is not None][:days],
            "degraded": [] if items else ["无可用历史数据"]}
