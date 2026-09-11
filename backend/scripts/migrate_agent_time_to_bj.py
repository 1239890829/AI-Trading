"""方案 A 一次性迁移：agent 域时间列 UTC naive → 北京 naive（+8h）。

背景：agent 域 7 张表的事件时间原先存 `utcnow()` naive，与全系统
「事件时间统一北京 naive」口径（app/core/bjtime.py，2026-09-09 定）不一致。
2026-09-12 用户批准方案 A：写入侧翻转为 beijing_now_naive 的同时，
把存量一次性 +8h 迁移。**必须先停 8000 再 --apply**（停机窗口内无并发写入）。

用法（cwd=backend）：
    .venv/bin/python scripts/migrate_agent_time_to_bj.py            # dry-run
    .venv/bin/python scripts/migrate_agent_time_to_bj.py --apply    # 实际迁移

三重防呆：
1. marker 文件（data/.agent_time_migrated_20260912）存在即拒绝——防重复 +8h；
2. 迁移前断言各列 MAX ≤ utcnow+5min——若已有「未来值」说明数据不是 UTC，
   再跑会错上加错；
3. 默认 dry-run，--apply 显式开启；全程单事务，异常即回滚。

JSON 内嵌时间戳（experiments.baseline.taken_at /
agent_param_change.evidence.{shadow_started_at, shadow_verdict.at}）一并迁移：
停机前旧代码仍在写 UTC 值，只迁 datetime 列会留双口径。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/

from app.core.bjtime import BJ_OFFSET  # 与全系统同源，勿自建 +8h 偏移（守卫测试扫描源码文本）
REPO_ROOT = Path(__file__).resolve().parents[2]
MARKER = REPO_ROOT / "data" / ".agent_time_migrated_20260912"
DB_PATH = REPO_ROOT / "data" / "ashare.db"

# (table, pk, 列) —— 与 app/models/agent.py 一致（含账本曾漏掉的 audit.at / verification_date）
COLUMNS: list[tuple[str, str, str]] = [
    ("agent_task", "id", "created_at"),
    ("agent_task", "id", "started_at"),
    ("agent_task", "id", "finished_at"),
    ("agent_triage", "id", "created_at"),
    ("agent_param", "key", "updated_at"),
    ("agent_param_change", "id", "created_at"),
    ("agent_param_change", "id", "applied_at"),
    ("agent_param_change", "id", "rolled_back_at"),
    ("agent_agenda", "id", "created_at"),
    ("agent_agenda", "id", "finished_at"),
    ("agent_experiment", "id", "created_at"),
    ("agent_experiment", "id", "concluded_at"),
    ("agent_experiment", "id", "verification_date"),
    ("agent_audit", "id", "at"),
]

# JSON 内嵌时间戳：(table, pk列, json列, 键路径)
JSON_KEYS: list[tuple[str, str, str, list[str]]] = [
    ("agent_experiment", "id", "baseline", ["taken_at"]),
    ("agent_param_change", "id", "evidence", ["shadow_started_at"]),
    ("agent_param_change", "id", "evidence", ["shadow_verdict", "at"]),
]


def shift_iso(value: str) -> str | None:
    """无时区标记的 ISO 串 +8h；带时区/解析失败返回 None（不动）。"""
    s = value.strip()
    if s.endswith("Z") or s[-6] in "+-" and s[-3] == ":":
        return None
    try:
        d = datetime.fromisoformat(s)
    except ValueError:
        return None
    if d.tzinfo is not None:
        return None
    return (d + BJ_OFFSET).isoformat(timespec="seconds")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写库（默认 dry-run）")
    args = ap.parse_args()

    if MARKER.exists():
        print(f"[refuse] marker 已存在（{MARKER}），此前已迁移过，拒绝重复 +8h")
        return 2
    if not DB_PATH.exists():
        print(f"[refuse] 数据库不存在：{DB_PATH}")
        return 2

    conn = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True)
    cur = conn.cursor()
    utcnow = datetime.utcnow()

    plan: list[tuple[str, str, str, object, object]] = []  # (table, pk_col, col, pk, old)
    old_max: dict[tuple[str, str], datetime] = {}
    print(f"== dry-run={not args.apply}  DB={DB_PATH}")
    print(f"{'表':20} {'列':18} {'行数':>5}  min → max（迁移后）")
    total = 0
    for table, pk, col in COLUMNS:
        cur.execute(f"SELECT {pk}, {col} FROM {table} WHERE {col} IS NOT NULL")
        rows = cur.fetchall()
        if not rows:
            print(f"{table:20} {col:18} {0:>5}  (空)")
            continue
        vals = [datetime.fromisoformat(v) for _, v in rows]
        mx = max(vals)
        old_max[(table, col)] = mx
        if mx > utcnow + timedelta(minutes=5):
            print(f"[refuse] {table}.{col} MAX={mx} > utcnow——数据不像 UTC naive，")
            print("         说明已迁移或时钟异常，继续跑会双重 +8h。终止。")
            return 2
        shifted_min = (min(vals) + BJ_OFFSET).isoformat(sep=" ", timespec="seconds")
        shifted_max = (mx + BJ_OFFSET).isoformat(sep=" ", timespec="seconds")
        total += len(rows)
        plan.extend((table, pk, col, k, v) for k, v in rows)
        print(f"{table:20} {col:18} {len(rows):>5}  {shifted_min} → {shifted_max}")

    json_hits = 0
    for table, pk, jcol, keypath in JSON_KEYS:
        cur.execute(f"SELECT {pk}, {jcol} FROM {table} WHERE {jcol} LIKE '%{keypath[-1]}%'")
        for row_pk, raw in cur.fetchall():
            try:
                doc = json.loads(raw)
            except Exception:
                continue
            node = doc
            for k in keypath[:-1]:
                if not isinstance(node, dict) or k not in node:
                    node = None
                    break
                node = node[k]
            if not isinstance(node, dict) or keypath[-1] not in node:
                continue
            if shift_iso(str(node[keypath[-1]])):
                json_hits += 1
    conn.close()
    print(f"合计：datetime 值 {total} 个，JSON 内嵌时间戳 {json_hits} 个")

    if not args.apply:
        print("[dry-run] 未写库。停 8000 后加 --apply 执行。")
        return 0

    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()  # 前面只读连接已关，必须重建游标
    try:
        with conn:  # 单事务，异常回滚
            applied = 0
            for table, pk, col, row_pk, old in plan:
                new = (datetime.fromisoformat(old) + BJ_OFFSET).isoformat(sep=" ")
                cur.execute(
                    f"UPDATE {table} SET {col} = ? WHERE {pk} = ? AND {col} = ?",
                    (new, row_pk, old),
                )
                applied += cur.rowcount
            changed = 0
            for table, pk, jcol, keypath in JSON_KEYS:
                cur.execute(f"SELECT {pk}, {jcol} FROM {table} WHERE {jcol} LIKE '%{keypath[-1]}%'")
                for row_pk, raw in cur.fetchall():
                    try:
                        doc = json.loads(raw)
                    except Exception:
                        continue
                    node = doc
                    for k in keypath[:-1]:
                        if not isinstance(node, dict) or k not in node:
                            node = None
                            break
                        node = node[k]
                    if not isinstance(node, dict) or keypath[-1] not in node:
                        continue
                    new_v = shift_iso(str(node[keypath[-1]]))
                    if new_v:
                        node[keypath[-1]] = new_v
                        cur.execute(
                            f"UPDATE {table} SET {jcol} = ? WHERE {pk} = ?",
                            (json.dumps(doc, ensure_ascii=False), row_pk),
                        )
                        changed += 1
            # 回读验证：MAX 应落在「北京时间」而非 UTC（≥ utcnow 才对：北京=UTC+8）
            for table, pk, col in COLUMNS:
                cur.execute(f"SELECT MAX({col}) FROM {table}")
                mx = cur.fetchone()[0]
                if mx is None or (table, col) not in old_max:
                    continue
                want = (old_max[(table, col)] + BJ_OFFSET).isoformat(sep=" ")
                if mx != want:
                    raise RuntimeError(f"回读异常：{table}.{col} MAX={mx} != 预期 {want}")
            if applied != total:
                raise RuntimeError(f"实际更新 {applied} != 计划 {total}（存在并发改动？）")
        MARKER.write_text(json.dumps({
            "migrated_at": datetime.now().isoformat(timespec="seconds"),
            "datetime_values": total, "json_values_changed": changed,
        }, ensure_ascii=False, indent=2))
        print(f"[apply] 完成：datetime {total} 个 + JSON {changed} 个；marker 已写 {MARKER}")
        return 0
    except Exception as exc:
        print(f"[abort] 已回滚：{exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
