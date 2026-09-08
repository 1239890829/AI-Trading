"""marketdb 数据质量门（策略进化 P1 方向 4）。

在 sync_marketdb.py 已有的 8 条上游 SDK 检查（行数/主键/high≥low/OHLC 非负/
复权事件主键）之上补三类业务校验——这些是「结构合法但语义异常」的坑：

1. **近窗空值率**：OHLCV 任一字段为 NULL 的行（近 30 个自然日）——
   上游 dump 偶发缺列，结构检查查不出 NULL 语义缺失；
2. **复权序列单日涨跌超限**：daily_k_adj 的日环比 |pct| > 31%（北交所
   涨跌幅上限 30% + 容差）——前复权序列已剔除除权断崖，出现超限涨幅 =
   数据错误（原始 daily_k 的跳变可能是除权，不能当异常）；
3. **报告落盘**：每次同步写 data/marketdb/quality_report.json，端点
   GET /api/system/marketdb-quality 读它（进程外可见性）。

三态纪律：warn 级不判同步失败（真实世界存在合法边界样本），但必须可见；
error 级维持上游语义（同步退出码 4）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import duckdb

#: 近窗检查覆盖的自然日数（~20 个交易日，覆盖 daily-k-10d 两次增量间隔有余）
RECENT_WINDOW_DAYS = 30
#: 复权序列单日涨跌硬上限（%）：北交所 30% + 四舍五入/容差
ADJ_JUMP_PCT = 31.0

#: (check 名, severity, SQL)。SQL 返回行 = 违规（沿用 sync_marketdb 的既有契约）。
RECENT_CHECKS: list[tuple[str, str, str]] = [
    ("daily_k.recent_null_ohlcv", "warn",
     f"""
     SELECT 'null fields', COUNT(*) FROM daily_k
     WHERE date_ms >= (SELECT MAX(date_ms) FROM daily_k) - {RECENT_WINDOW_DAYS * 86400_000}
       AND (open_price IS NULL OR high_price IS NULL OR low_price IS NULL
            OR close_price IS NULL OR volume IS NULL)
     HAVING COUNT(*) > 0
     """),
    ("daily_k_adj.single_day_jump", "warn",
     f"""
     WITH prev AS (
         SELECT thscode, date_ms, close_adj,
                LAG(close_adj) OVER (PARTITION BY thscode ORDER BY date_ms) AS p
         FROM daily_k_adj
         WHERE date_ms >= (SELECT MAX(date_ms) FROM daily_k_adj) - {RECENT_WINDOW_DAYS * 86400_000}
     )
     SELECT thscode, date_ms, ROUND((close_adj / p - 1) * 100, 2) AS pct
     FROM prev
     WHERE p > 0 AND close_adj IS NOT NULL AND ABS(close_adj / p - 1) * 100 > {ADJ_JUMP_PCT}
     LIMIT 10
     """),
    ("daily_k.recent_zero_close", "warn",
     f"""
     SELECT thscode, date_ms, close_price FROM daily_k
     WHERE date_ms >= (SELECT MAX(date_ms) FROM daily_k) - {RECENT_WINDOW_DAYS * 86400_000}
       AND close_price = 0
     LIMIT 10
     """),
]


def run_recent_quality_checks(con: duckdb.DuckDBPyConnection) -> list[dict]:
    """跑近窗业务校验。返回违规列表（空 = 通过）；单条检查失败显式记为 error。"""
    issues: list[dict] = []
    for name, severity, sql in RECENT_CHECKS:
        try:
            rows = con.execute(sql).fetchall()
        except Exception as exc:  # noqa: BLE001 —— 检查自身失败也是显式问题
            issues.append({"check": name, "severity": "error",
                           "detail": f"check failed: {exc}", "sample": []})
            continue
        if rows:
            issues.append({
                "check": name, "severity": severity,
                "detail": f"{len(rows)} 行违规（sample 首条）",
                "sample": [list(r) for r in rows[:10]],
            })
    return issues


def write_quality_report(db_path: Path, report: dict, issues: list[dict]) -> dict:
    """汇总结构检查 + 近窗检查 → quality_report.json。返回落盘的报告体。"""
    errors = [i for i in issues if i["severity"] == "error"]
    warns = [i for i in issues if i["severity"] == "warn"]
    status = "fail" if errors else ("warn" if warns else "ok")
    body = {
        "available": True,
        "status": status,
        "synced_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "db": str(db_path),
        "sync": report,
        "issues": issues,
        "error_count": len(errors),
        "warn_count": len(warns),
    }
    out_file = db_path.parent / "quality_report.json"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    return body


def append_sync_history(db_path: Path, entry: dict, keep: int = 30) -> None:
    """同步时长趋势落盘（/api/system/metrics 读）。写失败不影响同步结果。"""
    hist_file = db_path.parent / "sync_history.json"
    try:
        rows = json.loads(hist_file.read_text(encoding="utf-8")) if hist_file.exists() else []
    except Exception:  # noqa: BLE001  损坏即重开（趋势账本，非审计账本）
        rows = []
    rows.append(entry)
    hist_file.parent.mkdir(parents=True, exist_ok=True)
    hist_file.write_text(json.dumps(rows[-keep:], ensure_ascii=False), encoding="utf-8")
