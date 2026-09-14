"""个股辨识度画像（P2-37 二期 C）：「前期有过主力资金强拉升、具备人气基础」的量化。

需求出处（2026-09-13）：板块走强时自动筛出其中**有辨识度**的个股并持续跟踪。
与 ``intraday_opportunity.distinctiveness``（人气榜名次×连板高度×角色，**当日**口径）
互补：本模块是**历史画像**——用已落库的历史数据回答「这票此前有没有被资金做过、
有没有人气积累」，不依赖 theme_core 的 60 日归因样本（P1-7 样本阻塞不拦这条路）。

数据三源（全部已落库，零新增外呼）：
- marketdb daily_k（DuckDB，thscode 形如 ``600519.SH``）：近 20 日放量大涨次数 /
  区间涨幅 / 距 20 日高回撤；近 60 根 K 线内涨幅 ≥9.8% 的次数（``up_98pct_60d``）；
- skyrocket.jsonl（B1 飙升榜前向积累）：近 60 自然日入榜次数；
- lhb 归档（data/lhb/，E4 前向积累）：近 N 个已归档日上榜次数。

⚠️ ``up_98pct_60d`` 是**固定 9.8% 阈值**的涨幅计数，**未按板块区分**（20cm 板应 19.8%、
北交所应 30%，现会漏计）。**2026-09-14 只改名不改值**：纠正为诚实命名；把阈值接到
``pre_limit_radar.board_limit_pct`` 单点会改变排序数值，属**口径变更**，待拍板后再动。

⚠️ **权重为初始参数、未经实证**（与 board_surge 触发阈值同纪律）：分项全部随结果
暴露，校准前不得作为独立决策依据，只作「辨识度候选」排序参考。

三态纪律：marketdb 缺仓/成员缺码 → 显式 unavailable/missing，绝不拿 0 分冒充「无画像」。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

import duckdb

from app.core.bjtime import BJ_TZ, beijing_now

log = logging.getLogger(__name__)

#: 默认与 lurk_pool.DB 同源（backend/data/marketdb/market.duckdb）
MARKETDB_PATH = Path(__file__).resolve().parents[2] / "data" / "marketdb" / "market.duckdb"
SKYROCKET_PATH = Path("data/picks/heat/skyrocket.jsonl")
LHB_DIR = Path("data/lhb")

DAY_MS = 86_400_000

#: ⚠️ **两个时间单位不可混用**（R26，2026-09-14 修）：
#: ``daily_k.date_ms`` 是 epoch **毫秒**（走 ``DAY_MS``）；
#: 而 ``skyrocket.jsonl`` 的 ``date`` 是**日期字符串**，解析后是 epoch **秒**（走本常量）。
#: 原实现把「秒」拿去减 ``days * DAY_MS``（毫秒），截断点被推到约 **1962 年**
#: ⇒ 任何历史记录都满足 ``ts >= cutoff``，60 日窗口形同失效（合成 2000 年记录亦计入）。
_SECONDS_PER_DAY = 86_400

#: 涨停计数的窗口按 **K 线根数**而非自然日：60 自然日只有约 40 根（含周末），
#: 与「近 60 日」的语义不符（R26 同批修正）。
LOOKBACK_BARS = 60
#: 取数窗口（**自然日**）：留足裕量以保证至少 ``LOOKBACK_BARS`` 根
#: （60 交易日 ≈ 88 自然日，含长假故取 120）。
FETCH_DAYS = 120
LOOKBACK_MS = FETCH_DAYS * DAY_MS

#: 分项权重（初始值；校准前只作排序参考）
W_SPIKE, W_RET, W_HEAT, W_LIMIT, W_LHB, W_NEWHIGH = 20.0, 20.0, 15.0, 15.0, 20.0, 10.0


def _ths_code(symbol: str) -> str | None:
    sym = str(symbol or "").strip()
    if len(sym) != 6 or not sym.isdigit():
        return None
    if sym.startswith("6"):
        return f"{sym}.SH"
    if sym.startswith(("0", "3")):
        return f"{sym}.SZ"
    if sym.startswith(("4", "8", "9")):
        return f"{sym}.BJ"
    return None


def _median(xs: list[float]) -> float:
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return float("nan")
    mid = n // 2
    return xs[mid] if n % 2 else (xs[mid - 1] + xs[mid]) / 2.0


def load_klines(symbols: list[str], db_path: Path | None = None) -> dict[str, list[tuple]]:
    """批量取近 ``FETCH_DAYS`` 自然日内的日 K（未复权原始仓）。仓缺失 → {}（调用方按 unavailable 处理）。

    取数按**自然日**放宽，由 ``_kline_features`` 按 ``LOOKBACK_BARS`` **根数**裁剪，
    使「近 60 日」不受长假/停牌影响（R26）。
    """
    db = Path(db_path or MARKETDB_PATH)
    if not db.exists():
        return {}
    codes = [c for c in (_ths_code(s) for s in symbols) if c]
    if not codes:
        return {}
    placeholders = ",".join("?" * len(codes))
    con = duckdb.connect(str(db), read_only=True)
    try:
        rows = con.execute(
            f"select thscode, date_ms, close_price, volume from daily_k "
            f"where thscode in ({placeholders}) and date_ms >= ? order by thscode, date_ms",
            [*codes, int(beijing_now().timestamp() * 1000) - LOOKBACK_MS],
        ).fetchall()
    finally:
        con.close()
    out: dict[str, list[tuple]] = {}
    for code, t, c, v in rows:
        if c is None or v is None:
            continue
        out.setdefault(code, []).append((t, float(c), float(v)))
    return out


def _kline_features(bars: list[tuple]) -> dict:
    """日 K 序列 → 强拉升类分项（纯函数，可测）。bars = [(date_ms, close, volume)] 升序。

    涨停阈值**固定 9.8%**，故字段名为 ``up_98pct_60d``（不是「涨停次数」）——
    20cm 板（19.8%）与北交所（30%）会漏计，阈值单点化属口径变更、待拍板（R26）。
    窗口 = 最近 ``LOOKBACK_BARS`` **根**（不是 60 自然日）。
    """
    bars = bars[-LOOKBACK_BARS:]      # 按根数裁剪，见 LOOKBACK_BARS 注释
    closes = [b[1] for b in bars]
    vols = [b[2] for b in bars]
    n = len(bars)
    up_98_60 = sum(
        1 for i in range(1, n)
        if closes[i - 1] > 0 and (closes[i] / closes[i - 1] - 1) >= 0.098
    )
    # 近 20 根：放量大涨（涨 ≥5% 且 量 ≥ 2×前 5 根均量）
    spike_20 = 0
    for i in range(max(1, n - 20), n):
        if closes[i - 1] <= 0:
            continue
        pct = closes[i] / closes[i - 1] - 1
        ma5v = _median(vols[max(0, i - 5):i]) if i >= 1 else 0.0
        if pct >= 0.05 and ma5v > 0 and vols[i] >= 2.0 * ma5v:
            spike_20 += 1
    recent = closes[-20:] if n >= 20 else closes
    ret_20 = (recent[-1] / recent[0] - 1) * 100 if len(recent) >= 2 and recent[0] > 0 else 0.0
    high20 = max(recent) if recent else 0.0
    dd20 = (1 - closes[-1] / high20) * 100 if high20 > 0 else 0.0
    return {
        "up_98pct_60d": up_98_60,
        "vol_spike_20d": spike_20,
        "ret_20d": round(ret_20, 2),
        "drawdown_20d": round(dd20, 2),
    }


def load_skyrocket_hits(symbols: set[str], *, path: Path | None = None, days: int = 60) -> dict[str, int]:
    """飙升榜近 N 自然日入榜次数（文件缺失 → {}，口径=前向积累）。

    ⚠️ 单位纪律（R26）：``date`` 字符串解析后是 epoch **秒**，故截断点也用秒
    （``_SECONDS_PER_DAY``）。原实现用 ``days * DAY_MS``（毫秒）相减，截断点被推到
    约 1962 年 ⇒ 窗口恒真、任何历史记录都计入。
    """
    p = path or SKYROCKET_PATH
    if not p.exists():
        return {}
    cutoff = beijing_now().timestamp() - days * _SECONDS_PER_DAY
    hits: dict[str, int] = {}
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            d = row.get("date") or ""
            try:
                # 榜单日期按**北京日**归因（落地方写的是北京日期字符串）
                ts = datetime.fromisoformat(d).replace(tzinfo=BJ_TZ).timestamp()
            except Exception:  # noqa: BLE001
                continue
            if ts < cutoff:
                continue
            sym = row.get("symbol")
            if sym in symbols:
                hits[sym] = hits.get(sym, 0) + 1
    except Exception:  # noqa: BLE001
        log.warning("skyrocket 读取失败：%s", p, exc_info=True)
    return hits


def score_candidates(
    symbols: list[str],
    *,
    db_path: Path | None = None,
    skyrocket_path: Path | None = None,
    lhb_base_dir: Path | None = None,
) -> dict:
    """题材成员 → 辨识度候选（Top 全量打分，调用方排序取头）。

    返回 {available: bool, marketdb_note: str|None, items: [{symbol, score, breakdown}]}。
    available=False = marketdb 缺仓（显式不可用态，items 为空——三态纪律）。
    """
    kl = load_klines(symbols, db_path)
    if not kl and not Path(db_path or MARKETDB_PATH).exists():
        return {"available": False, "marketdb_note": "marketdb 仓不存在（先跑 scripts/sync_marketdb.py）",
                "items": []}
    sym_set = set(symbols)
    sky = load_skyrocket_hits(sym_set, path=skyrocket_path)
    from app.market import lhb_archive

    lhb = lhb_archive.count_symbol_hits(sym_set, base_dir=lhb_base_dir)
    lhb_hits = lhb["hits"]
    archived_days = lhb["archived_days"]

    items: list[dict] = []
    for sym in symbols:
        code = _ths_code(sym)
        bars = kl.get(code or "")
        if not bars:
            items.append({"symbol": sym, "score": None,
                          "note": "marketdb 无该码（未同步/北交所缺口）"})
            continue
        f = _kline_features(bars)
        s_spike = min(f["vol_spike_20d"], 4) / 4 * W_SPIKE
        s_ret = min(max(f["ret_20d"], 0.0), 30.0) / 30.0 * W_RET
        s_heat = min(sky.get(sym, 0), 6) / 6 * W_HEAT
        s_limit = min(f["up_98pct_60d"], 6) / 6 * W_LIMIT
        s_lhb = min(lhb_hits.get(sym, 0), 6) / 6 * W_LHB
        s_newhigh = W_NEWHIGH if f["drawdown_20d"] <= 10.0 else max(0.0, W_NEWHIGH - f["drawdown_20d"] / 2)
        score = round(s_spike + s_ret + s_heat + s_limit + s_lhb + s_newhigh, 1)
        items.append({
            "symbol": sym, "score": score,
            "breakdown": {
                "vol_spike_20d": f["vol_spike_20d"], "ret_20d": f["ret_20d"],
                "drawdown_20d": f["drawdown_20d"], "up_98pct_60d": f["up_98pct_60d"],
                "skyrocket_60d": sky.get(sym, 0), "lhb_archived_hits": lhb_hits.get(sym, 0),
            },
        })
    items.sort(key=lambda x: -(x["score"] if x["score"] is not None else -1))
    return {"available": True, "marketdb_note": None, "items": items,
            "lhb_archived_days": archived_days,
            "weights_calibrated": False}


def format_candidates(candidates: dict, names: dict[str, str] | None = None, limit: int = 5) -> str | None:
    """打分结果 → 告警文本行（无可用画像 → None，不输出空行）。"""
    if not candidates.get("available"):
        return None
    scored = [i for i in candidates.get("items", []) if i.get("score") is not None][:limit]
    if not scored:
        return None
    names = names or {}
    parts = [f"{names.get(i['symbol'], i['symbol'])}({i['score']:.0f})" for i in scored]
    return "辨识度候选：" + " ".join(parts)
