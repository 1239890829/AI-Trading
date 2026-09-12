"""P1-42 事件因子评估通道·第 0 步：触发样本量计量（2026-09-13，可复跑）。

用途：回答账本 P1-42 的前置问题——「先量『触发数能否够 120』，再决定是否值得建模」。
设计已定：0/1 事件因子不走 RankIC（截面连续值假设不成立），改走**事件研究法**
（触发日 vs 全市场基线的前瞻收益差 + 样本 ≥120 + walk-forward），评估器未建之前
先看库里的样本够不够。

口径（只读，不写库）：
- 样本单元 = `event_direction` 中 `direction ∈ {-1, +1}` 的行（0=仅确认关联不猜，不触发）；
- 「前瞻窗口已闭合」用日历日近似：`published_at <= 今天 - 10 天`（≈7 个交易日，
  覆盖 T+5 最短评估窗；偏保守——只会少算不会高估可用样本）；
- `distinct (触发日, target)` 单列，观察同日同标的重复触发的浓度（事件研究法里
  它们不独立，是 walk-forward 分层时要处理的口径）。

判定（照抄 P1-42）：闭合样本 ≥120 → 值得启动评估器建模；否则维持「等时间」，
按当前积累速率给出预计达标日期。输出 Markdown 段落，可直接贴进账本/日报。
"""
from __future__ import annotations

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.core.bjtime import beijing_today  # noqa: E402  ——「今天」唯一权威（.today() 守卫在扫描 scripts）

DB = Path(__file__).resolve().parents[2] / "data" / "ashare.db"

THRESHOLD = 120          # P1-42 设计的最小样本
CLOSE_WINDOW_DAYS = 10   # 前瞻窗口闭合的日历日近似（≈7 交易日，覆盖 T+5）


def _rows(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[tuple]:
    return con.execute(sql, params).fetchall()


def main() -> int:
    if not DB.exists():
        print(f"ERROR: 生产库不存在：{DB}", file=sys.stderr)
        return 1
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        total_rows, = _rows(con, "SELECT COUNT(*) FROM event_direction")[0]
        signed, = _rows(con, "SELECT COUNT(*) FROM event_direction WHERE direction != 0")[0]
        by_type = _rows(con, (
            "SELECT target_type, SUM(direction != 0), COUNT(*) FROM event_direction "
            "GROUP BY target_type ORDER BY target_type"
        ))
        distinct_events, = _rows(con, (
            "SELECT COUNT(DISTINCT event_id) FROM event_direction WHERE direction != 0"
        ))[0]
        span = _rows(con, (
            "SELECT MIN(DATE(e.published_at)), MAX(DATE(e.published_at)) FROM event_direction d "
            "JOIN event_card e ON e.id = d.event_id WHERE d.direction != 0"
        ))[0]
        closed, = _rows(
            con,
            "SELECT COUNT(*) FROM event_direction d JOIN event_card e ON e.id = d.event_id "
            "WHERE d.direction != 0 AND DATE(e.published_at) <= DATE('now', ?)",
            (f"-{CLOSE_WINDOW_DAYS} day",),
        )[0]
        recent7, = _rows(
            con,
            "SELECT COUNT(*) FROM event_direction d JOIN event_card e ON e.id = d.event_id "
            "WHERE d.direction != 0 AND DATE(e.published_at) > DATE('now', ?)",
            ("-7 day",),
        )[0]
        dup, = _rows(con, (
            "SELECT COUNT(*) FROM (SELECT 1 FROM event_direction d "
            "JOIN event_card e ON e.id = d.event_id WHERE d.direction != 0 "
            "GROUP BY DATE(e.published_at), d.target HAVING COUNT(*) > 1)"
        ))[0]
    finally:
        con.close()

    print("## P1-42 事件触发样本计量（只读，{}）\n".format(beijing_today().isoformat()))
    print(f"- 方向行总计 {total_rows}，其中**带方向触发** {signed}"
          f"（distinct 事件 {distinct_events}）")
    for tt, s, total in by_type:
        print(f"  - target_type={tt}: 触发 {s} / 行 {total}")
    if span[0]:
        print(f"- 触发日跨度：{span[0]} ~ {span[1]}")
    print(f"- **前瞻窗口已闭合**（≥{CLOSE_WINDOW_DAYS} 日历日前）：**{closed}** 条"
          f" —— 对比建模门槛 {THRESHOLD}")
    print(f"- 近 7 日新增触发 {recent7} 条；同日同标的重复触发 {dup} 组"
          f"（walk-forward 分层时按非独立处理）")

    if closed >= THRESHOLD:
        print(f"\n**判定：闭合样本 {closed} ≥ {THRESHOLD}，可启动事件研究法评估器建模**"
              "（先只做评估器、不碰消费侧；市场中性口径复用 strategy_verify）。")
        return 0
    if signed <= 0:
        print("\n**判定：样本 0，维持「等时间」。**")
        return 0
    if span[0]:
        d0 = date.fromisoformat(span[0])
        d1 = date.fromisoformat(span[1])
        days = max((d1 - d0).days, 1)
        rate = signed / days
        need = (THRESHOLD - closed) / rate if rate > 0 else None
        if need is None:
            print("\n**判定：积累速率为 0，无法外推；维持「等时间」。**")
            return 0
        eta = beijing_today() + timedelta(days=need)
        print(f"\n**判定：闭合样本 {closed} < {THRESHOLD}，维持「等时间」。**"
              f" 积累速率 ≈{rate:.1f} 条/日，按此外推约 **{eta.isoformat()}** 达标"
              f"（日历日外推，节假日会使实际更晚）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
