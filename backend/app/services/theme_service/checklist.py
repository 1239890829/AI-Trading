"""介入条件清单：从看板 payload 解析某只股票的介入条件（纯函数，零 IO）。

`backend/app/services/theme_service.py` 的内部切片（IMP-005 批 4，2026-09-15 从 1383 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与前置注释与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app.services.theme_service` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

from app.services.dragon_service import entry_checklist
# ---------------------------------------------------------------- 介入条件清单（P1-13）

def entry_checklist_from_board(
    board: dict, symbol: str, *, market_phase: str | None = None,
) -> dict | None:
    """从题材看板 payload 解析某只股票的「介入条件清单」（纯函数，零 IO）。

    为什么复用看板而不是重新取数：`dragon_score` / 封单质量 / 题材阶段 / 角色
    这些输入**看板构建时已经算好**（ladder 行的 dragon、card 的 stage）。再取一遍
    等于把同一份计算做两次、并多打一轮上游。找不到该 symbol（不在任何题材梯队里，
    例如未涨停的候选股）→ None，由调用方决定降级口径。

    market_phase 必须由调用方给（市场层环境是全局的，不在题材看板里）。
    """
    sym = (symbol or "").strip()
    if not sym:
        return None
    for card in board.get("themes") or []:
        for row in card.get("ladder") or []:
            if (row.get("symbol") or "").strip() != sym:
                continue
            checklist = entry_checklist(
                symbol=sym,
                name=row.get("name"),
                boards=row.get("boards"),
                seal_amount=row.get("seal_amount"),
                float_market_cap=row.get("float_market_cap"),
                first_seal_time=row.get("first_seal_time"),
                turnover_rate=row.get("turnover_rate"),
                break_count=row.get("break_count"),
                theme=card.get("theme"),
                theme_stage=card.get("stage"),
                dragon=row.get("dragon"),
                market_phase=market_phase,
            )
            return {
                **checklist,
                "trade_date": board.get("trade_date"),
                "theme_stage_basis": card.get("stage_basis"),
                "role": row.get("role"),
                "dragon": row.get("dragon") or {},
                "sentiment": row.get("sentiment") or {},
                "health_note": card.get("health_note"),
                "risks": card.get("risks") or [],
            }
    return None
