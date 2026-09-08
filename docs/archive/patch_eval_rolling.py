#!/usr/bin/env python
"""eval_report.json 的 rolling 字段后处理。

背景：2026-09-07 首批 37 因子跑批时 evaluate_factor 尚无 rolling 输出
（滚动 250 日 IC，衰减监控主指标）。报告内已存 best_horizon 的完整日 IC
序列（daily_ic），可零成本后处理补算，无需重跑 SQL。

用法（cwd=backend/）：
    .venv/bin/python scripts/patch_eval_rolling.py

后续月度跑批的 evaluate_factor 已内置 rolling（新代码），本脚本仅作
历史报告迁移兜底。
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.factors.evaluate import (  # noqa: E402
    IC_ABS_MIN,
    ROLLING_WINDOW_DAYS,
    _mean,
    _std,
)


def rolling_stats(pts: list[list]) -> dict:
    """pts: [[ts, ic], ...]（eval_report daily_ic 的序列化形态）。"""
    tail = [(ts, ic) for ts, ic in pts][-ROLLING_WINDOW_DAYS:]
    ics = [ic for _, ic in tail]
    if not ics:
        return {"window_days": ROLLING_WINDOW_DAYS, "n_days": 0,
                "ic_mean": None, "icir": None, "sign_flip": False}
    m = _mean(ics)
    s = _std(ics)
    icir = round(m / s, 4) if m is not None and s and s > 0 else None
    return {
        "window_days": ROLLING_WINDOW_DAYS,
        "n_days": len(tail),
        "ic_mean": round(m, 4) if m is not None else None,
        "icir": icir,
        # sign_flip 语义与 evaluate_factor 内一致：滚动均值显著(|IC|≥0.02)且与全期反向
        "sign_flip": bool(
            m is not None and abs(m) >= IC_ABS_MIN
            and math.copysign(1, m) != math.copysign(1, _full_sign(pts))
        ),
    }


def _full_sign(pts: list[list]) -> float:
    ics = [ic for _, ic in pts]
    m = _mean(ics)
    return math.copysign(1, m) if m else 1.0


def main() -> None:
    report_path = Path("data/factors/eval_report.json")
    if not report_path.exists():
        print("eval_report.json not found")
        sys.exit(1)
    report = json.loads(report_path.read_text())
    patched = 0
    for f in report.get("factors", []):
        if "rolling" in f:
            continue  # 新代码产物已有
        series = (f.get("daily_ic") or {}).get(str(f.get("best_horizon"))) or []
        f["rolling"] = rolling_stats(series)
        patched += 1
    tmp = report_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2))
    tmp.replace(report_path)
    print(f"patched rolling for {patched} factors -> {report_path}")


if __name__ == "__main__":
    main()
