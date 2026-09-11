"""策略改动回放对比门禁（2026-09-08 用户指令：策略调整必须以回测数据为依据）。

C 类代码改动合入后（涉及 backend/app/picks/ 的文件），自动跑一次跨日回放，
与上一份基线（data/replay_baseline.json）量化对比：

- 对比指标：日均换手% / 平均持有天数 / 日均组合分（replay_picks stats 口径）
- 判定：换手恶化 >10% 或组合分下降 → verdict=degraded（提示下一议程复评，
  不自动回滚——回滚由 experiments 30 日劣化守护承担）
- 基线：每次回放后写回 data/replay_baseline.json（date/commit/stats）

口径诚实声明：回放只覆盖**梯队+技术**两维（消息/情绪/基本面/资金依赖当前
快照无法回填历史），组合收益与买点胜率需 T+3 验收——报告必须带此注记，
不得冒充全维对比。
"""
from __future__ import annotations

import json
import logging
import re
import subprocess
from pathlib import Path
from app.core.bjtime import beijing_now_naive  # S2-8 时区收敛

log = logging.getLogger(__name__)

BASELINE_PATH = Path(__file__).resolve().parents[2] / "data" / "replay_baseline.json"
REPLAY_SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "replay_picks.py"
#: 换手恶化阈值（%）与组合分下降阈值（分），超过即 verdict=degraded
TURNOVER_REGRESS_PCT = 10.0
SCORE_REGRESS_ABS = 1.0
_REPLAY_TIMEOUT = 900  # 10 日回放 + 门禁节流的宽裕上限


def _parse_stats(report: str) -> dict | None:
    """从回放报告 markdown 提取三指标（缺任一返回 None——解析失败不臆造）。"""
    def _grab(label: str) -> float | None:
        m = re.search(rf"\|\s*{label}\s*\|\s*([\d.+-]+)\s*\|", report)
        return float(m.group(1)) if m else None

    stats = {
        "avg_turnover_pct": _grab("日均换手%"),
        "avg_holding_days": _grab("平均持有天数"),
        "avg_score": _grab("日均组合分"),
    }
    return stats if all(v is not None for v in stats.values()) else None


def _load_baseline() -> dict | None:
    try:
        return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001  无基线 = 首跑只建档
        return None


def _save_baseline(stats: dict, commit: str, days: int) -> None:
    BASELINE_PATH.parent.mkdir(parents=True, exist_ok=True)
    BASELINE_PATH.write_text(json.dumps({
        "saved_at": beijing_now_naive().isoformat(),
        "commit": commit, "days": days, "stats": stats,
    }, ensure_ascii=False, indent=2), encoding="utf-8")


def run_comparison(days: int = 10, commit: str = "", force: bool = True) -> dict:
    """跑回放并与基线对比。返回 {ok, verdict, report, baseline_was}。"""
    baseline = _load_baseline()
    cmd = ["python3", str(REPLAY_SCRIPT), "--days", str(days), "--out", "/tmp/evo-replay-latest.md"]
    if force:
        cmd.append("--force")
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=_REPLAY_TIMEOUT,
                              cwd=str(REPLAY_SCRIPT.parent.parent))
    except subprocess.TimeoutExpired:
        return {"ok": False, "verdict": "error", "report": f"回放超时（>{_REPLAY_TIMEOUT}s）",
                "baseline_was": baseline}
    report_path = Path("/tmp/evo-replay-latest.md")
    report = report_path.read_text(encoding="utf-8") if report_path.exists() else (proc.stdout or "")
    if proc.returncode != 0:
        return {"ok": False, "verdict": "error",
                "report": f"回放失败 rc={proc.returncode}：{(proc.stderr or '')[:300]}",
                "baseline_was": baseline}

    stats = _parse_stats(report)
    if stats is None:
        return {"ok": False, "verdict": "error",
                "report": f"回放报告解析失败（stats 缺失）。\n{report[:500]}",
                "baseline_was": baseline}

    if baseline is None or "stats" not in baseline:
        _save_baseline(stats, commit, days)
        return {"ok": True, "verdict": "baseline_created",
                "report": f"首次回放建档（无旧基线可比）。\n\n{report}",
                "stats": stats, "baseline_was": None}

    old = baseline["stats"]
    d_turnover = stats["avg_turnover_pct"] - old["avg_turnover_pct"]
    d_score = stats["avg_score"] - old["avg_score"]
    degraded = (
        d_turnover > TURNOVER_REGRESS_PCT and old["avg_turnover_pct"] > 0
        or d_score < -SCORE_REGRESS_ABS
    )
    _save_baseline(stats, commit, days)
    verdict = "degraded" if degraded else "ok"
    return {
        "ok": True, "verdict": verdict,
        "report": (
            f"回放对比（{days} 交易日，vs 基线 {baseline.get('commit', '?')}）\n"
            f"· 日均换手：{old['avg_turnover_pct']}% → {stats['avg_turnover_pct']}%（{d_turnover:+.1f}pp）\n"
            f"· 平均持有：{old['avg_holding_days']} → {stats['avg_holding_days']} 天\n"
            f"· 日均组合分：{old['avg_score']} → {stats['avg_score']}（{d_score:+.1f}）\n"
            f"· 判定：{verdict}\n\n{report}"
        ),
        "stats": stats, "deltas": {"turnover_pp": round(d_turnover, 2), "score": round(d_score, 2)},
        "baseline_was": baseline,
    }
