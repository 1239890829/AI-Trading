"""板块资金流服务（L2 枢纽）——「板块级资金」的唯一实现（docs/summary/architecture-design.md §2）。

## 层级纪律（R1 三层命名统一）
- L1 大盘   app/market/fund_flow.py           → /market/fund-flow*       （保持不动）
- L2 板块   本模块                             → /market/board-fund-flow* （唯一实现）
- L3 题材   theme_service.match_board 映射     → 消费 L2，不二次封装
- L4 个股   sina capital-flow（provider 链）   → /capital-flow/{symbol}
任何模块需要板块级资金数据只允许从本模块取数，**不得自行请求东财板块接口**
（防第二实现 + 双倍上游压力）。

## 数据源（2026-09-07 盘中逐项实测）
| 能力 | 接口 | 实测结论 |
|---|---|---|
| 板块现值列表 | clist fs=m:90+t:3/2, fid=f62 | f62 今日主力净额 / f66 超大单 / f69 超大占成交比 / f184 主力占成交比 / f164,f165 5日 / f174,f175 10日全部有值；单页上限 100 必须按 total 翻页 |
| 板块日度五档 | push2his fflow daykline secid=90.BKxxxx lmt=40 | 间歇拦截（HTTP 000 概率性）→ 重试 + 落盘为准 |
| 板块分钟分时 | push2delay fflow kline klt=1 | 延迟 ~15min 口径，显式标注 |
| 板块成员排行 | clist fs=b:BKxxxx fid=f62 | f62/f66/f72/f184 齐全（f184=净额/成交额×100） |

## 口径（红线）
- 主力=超大+大（东财真实口径）；板块 f62 是东财官方板块口径，不与个股/新浪口径混算。
- **f164/f174 为官方字段非推断**：实测 = 今日 f62 + 近 N-1 根完成日 bar 主力之和
  （BK1650 两窗口偏差均 0.00 亿，2026-09-07 盘中）；f160/f109/f110 等涨跌幅推断字段不使用。
- f184 主力净占比 = 主力净额/成交额×100（两板块样本与自算一致）；字段缺席时按同式自算（同义同值）。
- 三态：缺失 → None + degraded，绝不填 0；连续流入判不出 → None（区别于 0=今日净流出）。
- 双域 failover：push2 → push2delay（延迟口径显式标注），全败 available=False。

## 落盘（读路径零外呼，性能第一）
- data/boardflow/daily.json：收盘后（≥15:05）快照两 kind Top50 榜位 + Top50 行 + Top20/kind 成员
  Top20；保留 60 交易日。排名Δ = 今日榜位 − 昨日落盘榜位（正=上升）。
- data/boardflow/daykline.json：Top20/kind 板块日度主力净额序列（≤120 bar/板块，收盘快照时刷新）。
  连续流入天数与 20 日区间只从这里算，读路径不外呼。
- 紧凑数组落盘：boards=[code,name,主力净额亿,主力占比,涨跌幅]；members=[code,name,主力净额亿,主力占比]。
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path

from app.core.ttl_cache import TTLCache
from app.market import trade_calendar

log = logging.getLogger(__name__)

_YI = 1e8
from app.core.bjtime import beijing_now  # S2-8 时区收敛

_STORE_DIR = Path(__file__).resolve().parents[2] / "data" / "boardflow"
_DAILY_STORE = _STORE_DIR / "daily.json"
_DAYK_STORE = _STORE_DIR / "daykline.json"

#: 主域 → 备域（延迟口径）。2026-09-07 实测主域再次 HTTP 000，failover 是硬要求。
_HOSTS = ("https://push2.eastmoney.com", "https://push2delay.eastmoney.com")
_REFERER = "https://quote.eastmoney.com/"

_BOARD_LIST_FIELDS = "f12,f14,f3,f6,f62,f66,f69,f184,f104,f105,f128,f140,f164,f165,f174,f175"
_MEMBER_FIELDS = "f12,f14,f2,f3,f62,f66,f72,f184"
_KIND_FS = {"concept": "m:90+t:3+f:!50", "industry": "m:90+t:2+f:!50"}

#: 板块现值缓存：盘中 30s（一次翻页全市场拉齐，前端排序不回源）/ 盘外 300s
_LIST_CACHE = TTLCache("boardflow-list", ttl=30.0, maxsize=4)
_LIST_EOD_CACHE = TTLCache("boardflow-list-eod", ttl=300.0, maxsize=4)
#: 单板块分钟/成员：下钻时才拉，小容量防驻留
_MINUTE_CACHE = TTLCache("boardflow-minute", ttl=60.0, maxsize=4)
_MEMBERS_CACHE = TTLCache("boardflow-members", ttl=60.0, maxsize=8)
#: 板块 daykline 内存层（真正增量在落盘文件）
_DAYK_MEM_CACHE = TTLCache("boardflow-dayk", ttl=6 * 3600.0, maxsize=200)

_HTTP = None


def _http():
    global _HTTP
    if _HTTP is None:
        import httpx

        _HTTP = httpx.AsyncClient(
            trust_env=False,
            timeout=8.0,
            headers={"User-Agent": "Mozilla/5.0", "Referer": _REFERER, "Connection": "close"},
        )
    return _HTTP


def _in_session() -> bool:
    """2026-09-07 R2 收口：时刻判定单点在 trade_calendar.in_wide_market_window。"""
    return trade_calendar.in_wide_market_window(beijing_now())


def _num(v) -> float | None:
    if v in (None, "-", ""):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_yi(v) -> float | None:
    n = _num(v)
    return round(n / _YI, 2) if n is not None else None


def _int(v) -> int | None:
    n = _num(v)
    return int(n) if n is not None else None


def _clean_str(v) -> str | None:
    """东财占位符 '-' 视为缺失（f128 领涨股名等字符串字段）。"""
    if v in (None, "", "-"):
        return None
    return str(v)


# ---------------------------------------------------------------- clist（双域 failover）

async def _clist_pages(fs: str, fields: str, fid: str = "f62", pz: int = 100) -> tuple[list[dict] | None, str | None]:
    """全量翻页拉 clist（单页上限 100，按 total 翻页）。返回 (diff, 命中域名)，双域全败 (None, None)。"""
    last_exc: Exception | None = None
    for host in _HOSTS:
        try:

            async def _page(pn: int) -> dict:
                resp = await _http().get(
                    f"{host}/api/qt/clist/get",
                    params={
                        "pn": str(pn), "pz": str(pz), "po": "1", "np": "1",
                        "fltt": "2", "invt": "2", "fid": fid, "fs": fs, "fields": fields,
                    },
                    headers={"Referer": _REFERER},
                )
                resp.raise_for_status()
                return resp.json().get("data") or {}

            first = await _page(1)
            total = int(first.get("total") or 0)
            diff = list(first.get("diff") or [])
            pages = max(1, (total + pz - 1) // pz)
            if pages > 1:
                rest = await asyncio.gather(
                    *[_page(p) for p in range(2, pages + 1)], return_exceptions=True
                )
                for r in rest:
                    if isinstance(r, Exception):
                        raise r
                    # _page 返回的是内层 data dict（{total, diff}）
                    diff += list(r.get("diff") or [])
            if not diff:
                raise ValueError("empty diff")
            return diff, host
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            log.warning("board clist %s failed: %s", host, exc)
    log.warning("board clist all hosts failed: %s", last_exc)
    return None, None


def _board_rows_from_diff(diff: list[dict], kind: str) -> list[dict]:
    rows: list[dict] = []
    for it in diff:
        code, name = it.get("f12"), it.get("f14")
        if not code or not name:
            continue
        f62, f6 = _num(it.get("f62")), _num(it.get("f6"))
        ratio = _num(it.get("f184"))
        if ratio is None and f62 is not None and f6:
            ratio = round(f62 / f6 * 100, 2)  # 与 f184 同式（BK1650/BK0714 实测两位一致）
        rows.append(
            {
                "board_code": str(code),
                "name": str(name),
                "kind": kind,
                "change_pct": _num(it.get("f3")),
                "main_net_yi": _to_yi(f62),
                "main_net_ratio": ratio,
                "super_net_yi": _to_yi(it.get("f66")),
                "main_net_5d_yi": _to_yi(it.get("f164")),
                "main_net_10d_yi": _to_yi(it.get("f174")),
                "leader_name": _clean_str(it.get("f128")),
                "leader_symbol": _clean_str(it.get("f140")),
            }
        )
    return rows


async def get_board_list(kind: str) -> tuple[list[dict] | None, list[str]]:
    """全量板块行（concept/industry）。缓存盘中 30s / 盘外 300s；失败不缓存。"""
    if kind not in _KIND_FS:
        return None, [f"kind 非法：{kind!r}"]
    cache = _LIST_CACHE if _in_session() else _LIST_EOD_CACHE
    hit, cached = cache.get(kind)
    if hit:
        return cached, []
    diff, host = await _clist_pages(_KIND_FS[kind], _BOARD_LIST_FIELDS)
    if diff is None:
        return None, ["东财板块列表不可用（双域已重试）"]
    degraded: list[str] = []
    if host == _HOSTS[1]:
        degraded.append("主域不可达，使用延迟口径（push2delay）")
    rows = _board_rows_from_diff(diff, kind)
    if not rows:
        return None, degraded + ["板块列表解析为空"]
    cache.set(kind, rows)
    return rows, degraded


# ---------------------------------------------------------------- 板块下钻（分钟/成员）

async def get_board_minute(board_code: str) -> dict:
    """板块分钟五档资金流累计曲线（延迟 ~15min 口径）。复用 fund_flow 同一解析（R1）。"""
    hit, cached = _MINUTE_CACHE.get(board_code)
    if hit:
        return cached
    from app.market.fund_flow import _em_fflow_kline

    rows = await _em_fflow_kline(f"90.{board_code}")
    if rows is None:
        return {"available": False, "reason": "板块分钟资金流不可用（已重试）", "items": [], "degraded": []}
    items = [{"t": t, **vals} for t, vals in rows]
    # 日度主力净额序列（落盘，零外呼）：下钻抽屉的 5/10/20 日柱状 + 连续流入高亮。
    # 仅收盘快照沉淀过的 Top 板块有数据，其余空数组（三态，不外呼补）。
    bars = (_load_dayk_store().get(board_code) or {}).get("bars") or []
    out = {
        "available": True,
        "board_code": board_code,
        "items": items,  # [{t, main, small, mid, big, super_}]（亿元，累计口径）
        "daily_bars": [{"date": b[0], "main_yi": b[1], "close_pct": b[2]} for b in bars],
        "delayed": True,  # 延迟口径标志（前端必须标注）
        "updated_at": beijing_now().strftime("%H:%M:%S"),
        "degraded": [] if bars else ["日度历史未沉淀（每日收盘后自动累积 Top 板块）"],
    }
    _MINUTE_CACHE.set(board_code, out)
    return out


async def get_board_members(board_code: str) -> dict:
    """板块成员个股资金排行 Top20（fs=b:BKxxxx，fid=f62 服务端降序）。"""
    hit, cached = _MEMBERS_CACHE.get(board_code)
    if hit:
        return cached
    diff, host = await _clist_pages(f"b:{board_code}", _MEMBER_FIELDS, pz=20)
    if diff is None:
        return {"available": False, "reason": "板块成员资金排行不可用（双域已重试）", "rows": [], "degraded": []}
    rows: list[dict] = []
    for it in diff[:20]:
        code, name = it.get("f12"), it.get("f14")
        if not code or not name:
            continue
        rows.append(
            {
                "symbol": str(code),
                "name": str(name),
                "price": _num(it.get("f2")),
                "change_pct": _num(it.get("f3")),
                "main_net_yi": _to_yi(it.get("f62")),
                "super_net_yi": _to_yi(it.get("f66")),
                "big_net_yi": _to_yi(it.get("f72")),
                "main_net_ratio": _num(it.get("f184")),
            }
        )
    out = {
        "available": bool(rows),
        "board_code": board_code,
        "rows": rows,
        "delayed": host == _HOSTS[1],
        "updated_at": beijing_now().strftime("%H:%M:%S"),
        "degraded": (["主域不可达，使用延迟口径（push2delay）"] if host == _HOSTS[1] else []),
    }
    if out["available"]:
        _MEMBERS_CACHE.set(board_code, out)
    return out


# ---------------------------------------------------------------- 落盘：读路径零外呼

_mem_memo: dict[str, tuple[float | None, dict]] = {"daily": (None, {}), "dayk": (None, {})}


def _read_store_memo(path: Path, key: str) -> dict:
    """mtime 记忆化读盘：文件没变不重复 parse（读路径性能）。"""
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return {}
    m, data = _mem_memo[key]
    if m == mtime and data:
        return data
    try:
        data = json.loads(path.read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001
        data = {}
    _mem_memo[key] = (mtime, data)
    return data


def _write_json_atomic(path: Path, payload: dict) -> bool:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
        return True
    except Exception:  # noqa: BLE001
        log.warning("boardflow store persist failed: %s", path, exc_info=True)
        return False


def _load_dayk_store() -> dict:
    return _read_store_memo(_DAYK_STORE, "dayk").get("boards") or {}


def _yesterday_ranks(kind: str) -> tuple[dict | None, str | None]:
    """最近一个已落盘交易日（< 今日）的板块榜位。返回 (ranks, 日期)。"""
    days = _read_store_memo(_DAILY_STORE, "daily").get("days") or {}
    today = beijing_now().date().isoformat()
    for d in sorted(days, reverse=True):
        if d >= today:
            continue
        ranks = (days[d].get("ranks") or {}).get(kind)
        if ranks:
            return ranks, d
    return None, None


def _streak_from_bars(bars: list, f62_today: float | None, today_str: str) -> int | None:
    """连续净流入天数（含最新交易日）。bars 末根不含今日时用 f62 补（非交易日 f62=最近交易日终值，同值）。

    返回 None = 最新交易日归属判不出（三态，绝不猜 0）。
    """
    if not bars:
        return None
    vals = [b[1] for b in bars]
    if bars[-1][0] != today_str:
        if f62_today is None:
            return None
        vals = vals + [f62_today]
    n = 0
    for v in reversed(vals):
        if v is None or v <= 0:
            break
        n += 1
    return n


def _sum_n_yi(bars: list, n: int, f62_today: float | None, today_str: str) -> float | None:
    """近 n 个交易日主力净额合计（含最新交易日）。bars 已含今日 → Σ last n；否则 f62 + Σ last n-1。"""
    if not bars:
        return None
    vals = [b[1] for b in bars]
    if bars[-1][0] != today_str:
        if f62_today is None:
            return None
        vals = vals + [f62_today]
    window = [v for v in vals[-n:] if v is not None]
    if len(window) < n:
        return None  # 窗口不足 → None（不凑数）
    return round(sum(window), 2)


def get_board_streaks(f62_by_code: dict[str, float | None]) -> dict[str, int | None]:
    """从落盘 daykline 批量算连续流入天数（零外呼）。store 无该板块 → 不在返回里。"""
    store = _load_dayk_store()
    today_str = beijing_now().date().isoformat()
    out: dict[str, int | None] = {}
    for code, bars in store.items():
        bars = bars.get("bars") or []
        f62 = f62_by_code.get(code)
        s = _streak_from_bars(bars, f62, today_str)
        if s is not None:
            out[code] = s
    return out


# ---------------------------------------------------------------- 主入口：板块资金流榜

_RANGE_SORT_KEY = {
    "intraday": "main_net_yi",
    "5d": "main_net_5d_yi",
    "10d": "main_net_10d_yi",
}


async def get_board_fund_flow(kind: str, range_key: str) -> dict:
    """板块资金流榜（前端只切参数；排序/筛选纯内存）。

    - intraday/5d/10d：一次翻页全量（clist f62/f164/f174），零额外上游调用；
    - 20d：只读落盘 daykline 自算（读路径零外呼），未沉淀板块不参与排名，显式 coverage；
    - 连续流入天数：只对落盘板块可判，其余 None。
    """
    if range_key not in ("intraday", "5d", "10d", "20d"):
        return {"available": False, "reason": f"range 非法：{range_key!r}", "rows": [], "degraded": []}
    rows, degraded = await get_board_list(kind)
    if rows is None:
        return {"available": False, "reason": degraded[0] if degraded else "数据源不可用", "rows": [], "degraded": degraded}

    today_str = beijing_now().date().isoformat()

    if range_key == "20d":
        store = _load_dayk_store()
        if not store:
            return {
                "available": False,
                "reason": "板块 20 日历史尚未沉淀（每日收盘后自动累积 Top 板块）",
                "rows": [],
                "coverage": 0,
                "degraded": degraded,
            }
        enriched: list[dict] = []
        for r in rows:
            bars = (store.get(r["board_code"]) or {}).get("bars") or []
            s20 = _sum_n_yi(bars, 20, r.get("main_net_yi"), today_str)
            if s20 is None:
                continue
            out = dict(r)
            out["main_net_20d_yi"] = s20
            enriched.append(out)
        enriched.sort(key=lambda r: (r["main_net_20d_yi"] is None, -(r["main_net_20d_yi"] or 0)))
        for i, r in enumerate(enriched):
            r["rank"] = i + 1
            r["rank_delta"] = None  # 20 日区间无昨日落盘基线
        streaks = {r["board_code"]: None for r in enriched}
        store_streaks = get_board_streaks({r["board_code"]: r.get("main_net_yi") for r in enriched})
        streaks.update(store_streaks)
        for r in enriched:
            r["streak"] = streaks.get(r["board_code"])
        return {
            "available": True,
            "kind": kind,
            "range": range_key,
            "rows": enriched[:50],
            "total_boards": len(rows),
            "coverage": len(store),
            "updated_at": beijing_now().strftime("%H:%M:%S"),
            "rank_basis_date": None,
            "degraded": degraded + [f"20 日区间基于本地沉淀的 {len(store)} 个板块（每日收盘后自动扩充）"],
        }

    sort_key = _RANGE_SORT_KEY[range_key]
    rows.sort(key=lambda r: (r.get(sort_key) is None, -(r.get(sort_key) or 0)))
    rank_y, basis_date = (None, None)
    if range_key == "intraday":
        rank_y, basis_date = _yesterday_ranks(kind)
        if rank_y is None:
            degraded.append("昨日榜位未沉淀，排名Δ不可用")
    streaks = get_board_streaks({r["board_code"]: r.get("main_net_yi") for r in rows})
    if not streaks:
        degraded.append("连续流入列尚未沉淀（每日收盘后自动累积 Top 板块）")
    for i, r in enumerate(rows):
        r["rank"] = i + 1
        prev = rank_y.get(r["board_code"]) if rank_y else None
        # 昨日 Top50 之外的板块无基线 → None（三态，绝不冒充 0）
        r["rank_delta"] = (prev - r["rank"]) if prev is not None else None
        r["streak"] = streaks.get(r["board_code"])
    return {
        "available": True,
        "kind": kind,
        "range": range_key,
        "rows": rows,
        "total_boards": len(rows),
        "coverage": None,
        "updated_at": beijing_now().strftime("%H:%M:%S"),
        "rank_basis_date": basis_date,
        "degraded": degraded,
    }


# ---------------------------------------------------------------- 收盘快照（写路径）

_SNAPSHOT_HOUR = 15
_SNAPSHOT_MINUTE = 5
_TOP_BOARD_ROWS = 50      # 榜位/行快照深度（每 kind）
_TOP_MEMBER_BOARDS = 20   # 成员 Top20 + daykline 的沉淀深度（每 kind）
_DAYS_RETENTION = 60      # daily.json 保留交易日数
_BARS_RETENTION = 120     # daykline.json 每板块保留 bar 数


async def _fetch_board_dayk(board_code: str) -> list | None:
    """push2his fflow daykline → [[date, main_yi, close_pct]]（含今日 bar 由快照时机保证收盘后拉取）。重试 3 次。

    技术债豁免（同 fund_flow.py O3/P1-5 注记）：自建 httpx 重试不走 provider
    链统一熔断——板块级低频读与行情主链熔断域隔离；docs/system-health-review-20260907.html P1-5。
    """
    for attempt in range(3):
        try:
            resp = await _http().get(
                "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get",
                params={
                    "lmt": "40", "klt": "101", "secid": f"90.{board_code}",
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
                },
                headers={"Referer": _REFERER},
            )
            resp.raise_for_status()
            klines = ((resp.json().get("data") or {}).get("klines") or [])
            out: list = []
            for line in klines:
                parts = line.split(",")
                if len(parts) < 13:
                    continue
                main = _to_yi(parts[1])
                if main is None:
                    continue
                pct = _num(parts[12])
                out.append([parts[0], main, round(pct, 2) if pct is not None else None])
            if out:
                return out
            raise ValueError("empty klines")
        except Exception as exc:  # noqa: BLE001
            log.warning("board daykline %s attempt %s failed: %s", board_code, attempt + 1, exc)
            await asyncio.sleep(1.0)
    return None


async def _gather_bounded(coros, limit: int = 5):
    """有界并发执行协程集合（防 504 板块级请求打爆上游）。"""
    sem = asyncio.Semaphore(limit)

    async def _wrap(c):
        async with sem:
            res = await c
            await asyncio.sleep(0.2)  # 连接间隔，防熔断
            return res

    return await asyncio.gather(*[_wrap(c) for c in coros])


async def snapshot_daily_if_closed() -> bool:
    """收盘后（≥15:05）且当日未落盘时沉淀：两 kind Top50 榜位/行 + Top20/kind 成员 Top20 + daykline。

    由 review_intraday 调度 tick 调用（15:00-23:00 窗口内每分钟尝试，当日幂等）。
    返回是否写入。任何子项失败只跳过该子项并 warning，不阻塞其余沉淀。
    """
    now = beijing_now()
    if (now.hour, now.minute) < (_SNAPSHOT_HOUR, _SNAPSHOT_MINUTE):
        return False
    daily = _read_store_memo(_DAILY_STORE, "daily") or {"days": {}}
    days: dict = daily.get("days") or {}
    today = now.date().isoformat()
    if today in days:
        return False

    dayk_store = _read_store_memo(_DAYK_STORE, "dayk") or {"boards": {}}
    boards_store: dict = dayk_store.get("boards") or {}

    snap_day: dict = {"ranks": {}, "boards": {}, "members": {}}
    for kind in ("concept", "industry"):
        rows, _deg = await get_board_list(kind)
        if not rows:
            log.warning("boardflow snapshot: %s rows unavailable", kind)
            continue
        rows = sorted(rows, key=lambda r: (r.get("main_net_yi") is None, -(r.get("main_net_yi") or 0)))
        top = rows[:_TOP_BOARD_ROWS]
        snap_day["ranks"][kind] = {r["board_code"]: i + 1 for i, r in enumerate(top)}
        snap_day["boards"][kind] = [
            [r["board_code"], r["name"], r.get("main_net_yi"), r.get("main_net_ratio"), r.get("change_pct")]
            for r in top
        ]
        member_codes = [r["board_code"] for r in top[:_TOP_MEMBER_BOARDS]]

        members = await _gather_bounded([get_board_members(c) for c in member_codes])
        for code, m in zip(member_codes, members):
            if m and m.get("available"):
                snap_day["members"][code] = [
                    [x["symbol"], x["name"], x.get("main_net_yi"), x.get("main_net_ratio")] for x in m["rows"]
                ]

        dayks = await _gather_bounded([_fetch_board_dayk(c) for c in member_codes])
        for code, bars in zip(member_codes, dayks):
            if not bars:
                continue
            name = next((r["name"] for r in top if r["board_code"] == code), code)
            old = (boards_store.get(code) or {}).get("bars") or []
            merged = {b[0]: b for b in old}
            for b in bars:
                merged[b[0]] = b
            boards_store[code] = {
                "name": name,
                "kind": kind,
                "updated_at": now.isoformat(timespec="seconds"),
                "bars": sorted(merged.values(), key=lambda b: b[0])[-_BARS_RETENTION:],
            }

    days[today] = snap_day
    daily["days"] = {d: v for d, v in sorted(days.items())[-_DAYS_RETENTION:]}
    daily["updated_at"] = now.isoformat(timespec="seconds")
    dayk_store["boards"] = boards_store
    dayk_store["updated_at"] = now.isoformat(timespec="seconds")

    ok1 = _write_json_atomic(_DAILY_STORE, daily)
    ok2 = _write_json_atomic(_DAYK_STORE, dayk_store)
    if ok1 or ok2:
        _mem_memo["daily"] = (None, {})  # 强制下次重读
        _mem_memo["dayk"] = (None, {})
        log.info("boardflow daily snapshot saved for %s (daily=%s dayk=%s boards)", today, ok1, len(boards_store))
    return ok1 or ok2
