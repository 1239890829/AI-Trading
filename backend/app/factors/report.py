"""因子评估产物的**运行时只读访问层**（S2-11，2026-09-11）。

## 为什么需要它

`app/factors/evaluate.py::run_full_eval` 会产出 `data/factors/eval_report.json`
（37 个因子 × 4 个前瞻窗口的 IC / ICIR / 分位判定 + PASS/CONDITIONAL/FAIL 结论），
但收敛前**全站零运行时消费**：`evolution.py` 的 `factor_ic` 一路硬编码

    {"available": False, "note": "月度复核（factor_ic_review）到期接入"}

——评估跑完了却没人读，**闭环断在最后一米**。每日进化议程凑齐了九路证据，
其中一路永远写着「不可用」，而它本该是唯一能量化回答「哪些因子真的有效」的那一路。

本模块把「读报告」收口成一处，并**强制带新鲜度**：报告停在 09-07 就要如实说出
「已 4 天/40 天」，而不是把它当成"当天结论"继续用（红线 2：禁止把过期缓存冒充实盘）。

## 三态纪律

- 报告缺失 / 不可解析 → `{"available": False, "reason": ...}`，**绝不**返回空列表或 0；
- 报告存在但超期 → `available: True` + `stale: True` + `age_days`，由**消费方**决定是否降级
  （缓存层不吞异常、不替业务做决策——与 P1-3 共享情绪缓存槽同理）；
- 单因子缺字段 → `None`，不凑 0。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path

from app.core.bjtime import beijing_now_naive

log = logging.getLogger(__name__)

#: 评估报告路径（`evaluate.run_full_eval(out_path=...)` 的落盘位置）。
REPORT_PATH = Path(__file__).resolve().parents[2] / "data" / "factors" / "eval_report.json"

#: 月度复核口径的宽限期（天）。评估是月度跑一次，超过它就该显式提示"该重跑了"。
DEFAULT_MAX_AGE_DAYS = 40


def _current_algo_version() -> str:
    """代码**当前**的算法口径版本。

    延迟 import：只读层不必为了一次字符串比较把批处理模块（`evaluate`，全历史扫描引擎）
    拉进导入链。
    """
    from app.factors.evaluate import ALGO_VERSION

    return ALGO_VERSION


def load_report() -> dict | None:
    """读取评估报告。**不抛异常**——读不出来返回 None，由调用方决定降级姿势。"""
    try:
        if not REPORT_PATH.exists():
            return None
        data = json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001  报告损坏等同于缺失（红线：不拿坏数据充数）
        log.warning("factor report unreadable: %s", exc)
        return None
    return data if isinstance(data, dict) else None


def _age_days(generated_at: str | None) -> int | None:
    if not generated_at:
        return None
    try:
        dt = datetime.fromisoformat(str(generated_at).replace("Z", ""))
    except Exception:  # noqa: BLE001
        return None
    return (beijing_now_naive() - dt).days


def freshness(max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> dict:
    """报告新鲜度（三态：缺失 / 新鲜 / 超期）+ **口径版本维度**（GOV-001）。

    `stale` **只表示时间维度**（`generated_at` 超宽限），语义不变——既有消费方
    依赖它判断"该重跑了"。口径维度是**新增的独立字段**：

    - `algo_current=True`  报告口径 == 代码当前口径；
    - `algo_current=False` 口径**已变更** ⇒ 结论须复核后方可用（见 `algo_note`）；
    - `algo_current=None`  报告无 `algo_version` 字段（口径版本机制之前的产出）
      ⇒ **未判定**，不塌缩成 False（§1「三态 > 二态」纪律）。

    为什么必须分开：**未超期 ≠ 结论仍然成立**。实测活案例（2026-09-14）——磁盘报告
    产出于 09-07（无 `algo_version`），代码口径已到 `2026-09-14.ic-maturity-v3`，
    而仅按时间判定时 `age_days=7 < 40` ⇒ 旧口径结论照样被判"新鲜"送进进化议程。
    """
    rep = load_report()
    if rep is None:
        return {"available": False, "reason": f"评估报告缺失或不可解析：{REPORT_PATH}",
                "path": str(REPORT_PATH)}
    age = _age_days(rep.get("generated_at"))
    stale = age is None or age > max_age_days

    declared = rep.get("algo_version")
    declared = declared if isinstance(declared, str) and declared else None
    current = _current_algo_version()
    algo_current = None if declared is None else (declared == current)

    if algo_current is False:
        algo_note = (f"报告口径 {declared} 与代码当前 {current} 不一致 ⇒ "
                     f"结论须复核后方可用于调参（GOV-001）")
    elif algo_current is None:
        algo_note = "旧报告未声明口径版本 ⇒ 是否与当前口径同源**未判定**"
    else:
        algo_note = None

    return {
        "available": True,
        "stale": stale,
        "age_days": age,
        "max_age_days": max_age_days,
        "generated_at": rep.get("generated_at"),
        "reason": (None if not stale else
                   ("报告时间无法解析" if age is None else f"报告已 {age} 天，超过 {max_age_days} 天宽限")),
        # 口径维度（与时间维度正交，勿混用）
        "algo_version": declared,
        "algo_version_current": current,
        "algo_current": algo_current,
        "algo_note": algo_note,
        "path": str(REPORT_PATH),
    }


def _verdict_of(entry: dict, summary: dict) -> str:
    """单因子结论：优先取条目自带 `verdict`，否则回退到 summary 分类。"""
    v = entry.get("verdict")
    if isinstance(v, str) and v:
        return v.upper()
    name = entry.get("name")
    for bucket in ("pass", "conditional", "fail"):
        if name in (summary.get(bucket) or []):
            return {"pass": "PASS", "conditional": "CONDITIONAL", "fail": "FAIL"}[bucket]
    return "UNKNOWN"


def _best_window(entry: dict) -> dict | None:
    """取该因子 `best_horizon` 对应窗口的统计量；缺失返回 None（不凑 0）。"""
    hz = entry.get("best_horizon")
    wins = entry.get("windows") or {}
    if hz is None:
        # 兜底：取 |icir| 最大的窗口（与 evaluate 的选窗口径一致）
        best, best_abs = None, -1.0
        for k, w in wins.items():
            icir = (w or {}).get("icir")
            if isinstance(icir, (int, float)) and abs(icir) > best_abs:
                best, best_abs = k, abs(icir)
        if best is None:
            return None
        hz = best
    w = wins.get(str(hz))
    if not isinstance(w, dict):
        return None
    return {"horizon": hz, **w}


def top_factors(limit: int = 8, *, verdicts: tuple[str, ...] | None = None) -> list[dict]:
    """按 |ICIR| 降序返回因子及其最佳窗口统计。

    :param verdicts: 只保留这些结论（`("PASS",)` 表示只看通过门槛的）；
        `None` = 全部。
    """
    rep = load_report()
    if rep is None:
        return []
    summary = rep.get("summary") or {}
    out: list[dict] = []
    for entry in rep.get("factors") or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        verdict = _verdict_of(entry, summary)
        if verdicts is not None and verdict not in verdicts:
            continue
        w = _best_window(entry)
        if w is None:
            continue
        ic = w.get("ic_mean")
        out.append({
            "name": entry.get("name"),
            "category": entry.get("category"),
            "verdict": verdict,
            "horizon": w.get("horizon"),
            "ic_mean": ic,
            "icir": w.get("icir"),
            "coverage": entry.get("coverage"),
            "n_days": w.get("n_days"),
            # 方向 = IC 符号。负 IC 亦是有信息（A 股短周期动量常见反转），
            # 但**必须由实测 IC 决定**，不能用 `library.FactorDef.note` 里的"预期方向"。
            "direction": (1 if isinstance(ic, (int, float)) and ic > 0
                          else -1 if isinstance(ic, (int, float)) and ic < 0 else None),
        })
    out.sort(key=lambda r: abs(r["icir"] or 0.0), reverse=True)
    return out[:limit]


def ic_evidence(limit: int = 6, max_age_days: int = DEFAULT_MAX_AGE_DAYS) -> dict:
    """进化议程「factor_ic」证据路的载荷（替代原硬编码空值）。

    返回形状与其它 `_collect_*` 一致：`{"available": ..., ...}`。
    """
    fresh = freshness(max_age_days=max_age_days)
    if not fresh["available"]:
        return {"available": False, "note": fresh["reason"]}

    rep = load_report() or {}
    summary = rep.get("summary") or {}
    passed = top_factors(limit, verdicts=("PASS",))
    return {
        "available": True,
        "stale": fresh["stale"],
        "age_days": fresh["age_days"],
        "generated_at": fresh["generated_at"],
        "note": fresh["reason"],
        # 口径维度（GOV-001）：与 `stale`（时间）正交——「未超期」不等于「结论仍成立」。
        # 消费方（进化议程）据此判断这份因子结论是否与**当前口径**同源。
        "algo_version": fresh["algo_version"],
        "algo_current": fresh["algo_current"],
        "algo_note": fresh["algo_note"],
        "counts": {
            "pass": len(summary.get("pass") or []),
            "conditional": len(summary.get("conditional") or []),
            "fail": len(summary.get("fail") or []),
        },
        "universe": rep.get("universe") or {},
        # 只送通过门槛的最强因子——议程 prompt 有长度预算，37 条全塞进去会稀释信号
        "top": passed,
        "caveat": ("IC 由本地全历史回测算出，样本内结论；**未做样本外验证**前不得直接"
                   "当选股权重（准入三级态见 KB-DEC-019）"),
    }
