"""通知已读状态的读写与合并（2026-09-12 缺陷修复的服务层）。

## 合并纪律：**单调**，绝不回退

状态的三个分量各有合并规则，共同目标是「任何一侧都不会把另一侧已读的条目重新变未读」：

| 分量 | 合并 | 理由 |
|---|---|---|
| `seen_before`（已读水位） | `max` | 水位是「已读到哪一刻」，只前进 |
| `clear_before`（清除水位） | `max` | 同上；回退会让已清除的条目复活 |
| `read_ids`（逐条已读） | 并集 | 任一侧点开过就算已读 |

**为什么必须单调**：前端首次挂载时 localStorage 可能领先（离线点过已读、服务端
尚未收到），服务端也可能领先（换了 origin / 清了站点数据）。若直接「服务端覆盖本地」
或「本地覆盖服务端」，都会出现用户已读的消息**重新变未读**——这正是本次要修的缺陷形态。

## 为什么在服务端也做合并（而不是让前端算完再 PUT）

PUT 是**并发与重试**都会到达的写入口：两个标签页各自 PUT、请求重试、慢请求晚到，
都会造成「后到的旧值覆盖新值」。服务端按合并规则落库后**回传合并结果**，
前端以回传值为准，写-写冲突自然消解。
"""
from __future__ import annotations

import json
from typing import Any

from app.core.bjtime import beijing_now_naive
from app.core.db import get_session_factory
from app.models.notification import SINGLETON_ID, NotificationReadState

#: `read_ids` 上界。逐条已读只登记「晚于水位」的条目（水位覆盖的会被合并规则丢弃），
#: 正常情况下不会无限增长；此处只是防止异常客户端灌爆单行 JSON。
READ_IDS_MAX = 500


def merge_read_state(stored: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """纯函数合并（无 IO，便于单测）：单调水位 + 逐条 id 并集。"""
    seen = max(int(stored.get("seen_before") or 0), int(incoming.get("seen_before") or 0))
    clear = max(int(stored.get("clear_before") or 0), int(incoming.get("clear_before") or 0))

    merged_ids: list[str] = []
    seen_ids: set[str] = set()
    for raw in list(stored.get("read_ids") or []) + list(incoming.get("read_ids") or []):
        sid = str(raw)
        if sid and sid not in seen_ids:
            seen_ids.add(sid)
            merged_ids.append(sid)
    # 只保留尾部（最新登记的一批）：并集顺序是「先存量后增量」，末尾即较新
    if len(merged_ids) > READ_IDS_MAX:
        merged_ids = merged_ids[-READ_IDS_MAX:]

    return {"seen_before": seen, "read_ids": merged_ids, "clear_before": clear}


def _row_to_dict(row: NotificationReadState | None) -> dict[str, Any]:
    if row is None:
        return {"seen_before": 0, "read_ids": [], "clear_before": 0, "updated_at": None}
    try:
        ids = json.loads(row.read_ids or "[]")
    except Exception:  # noqa: BLE001  脏数据 → 退回空表，不炸读取路径
        ids = []
    return {
        "seen_before": int(row.seen_before or 0),
        "read_ids": [str(i) for i in ids] if isinstance(ids, list) else [],
        "clear_before": int(row.clear_before or 0),
        "updated_at": row.updated_at.isoformat(sep=" ") if row.updated_at else None,
    }


def load_state(session_factory=None) -> dict[str, Any]:
    """读取当前已读状态；无行 = 全空状态（不是错误）。"""
    sf = session_factory or get_session_factory()
    with sf() as db:
        row = db.get(NotificationReadState, SINGLETON_ID)
        return _row_to_dict(row)


def save_state(incoming: dict[str, Any], session_factory=None) -> dict[str, Any]:
    """合并后落库并回传合并结果（合并语义见模块 docstring）。"""
    sf = session_factory or get_session_factory()
    with sf() as db:
        row = db.get(NotificationReadState, SINGLETON_ID)
        merged = merge_read_state(_row_to_dict(row), incoming)
        if row is None:
            row = NotificationReadState(
                id=SINGLETON_ID,
                seen_before=merged["seen_before"],
                read_ids=json.dumps(merged["read_ids"], ensure_ascii=False),
                clear_before=merged["clear_before"],
            )
            db.add(row)
        else:
            row.seen_before = merged["seen_before"]
            row.read_ids = json.dumps(merged["read_ids"], ensure_ascii=False)
            row.clear_before = merged["clear_before"]
            row.updated_at = beijing_now_naive()
        db.commit()
        return _row_to_dict(row)
