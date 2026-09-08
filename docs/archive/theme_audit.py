"""题材概念标注全量审计与修复脚本（用户指令 2026-09-01：以同花顺官方为唯一基准）。

背景：theme_member 表依赖 sync_stale_members 每轮最多补 20 个题材，实际只覆盖了
38/390 个题材（90% 成分为空）——个股题材归属大面积缺失（如永鼎股份查不到光纤概念）。

流程（--fix 时写库，默认只审计）：
1. 实时拉取同花顺官方概念目录（390 个）与全部成分股（受控并发）→ symbol→[题材名] 权威映射
2. 与库内 theme_member 比对 → 漏标（实时有库无）/ 过期（库有实时无）两张明细
3. 个股视角差异明细（股票名称 / 我们当前标注 / 同花顺标注 / 修正动作）
4. 涨停池归因标签 × 实时官方成分 × 官方目录 三方校验（归因题材名是否官方存在、该股是否官方成分）
5. --fix 时按官方口径 upsert（直接调用 sync_members 逐题材写库，保持与 /api/themes/sync 同一代码路径）

用法：
  cd backend && .venv/bin/python scripts/theme_audit.py            # 只审计出报告
  cd backend && .venv/bin/python scripts/theme_audit.py --fix      # 审计 + 修复落库
报告输出：docs/theme-audit-<date>.md + stdout 摘要
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx

from app.services.theme_catalog_service import ThemeCatalogService, parse_catalog_items

ROOT = Path(__file__).resolve().parent.parent.parent
DB_PATH = ROOT / "data" / "ashare.db"


async def fetch_all_official(svc: ThemeCatalogService) -> tuple[dict[str, str], dict[str, set[str]], dict[str, list[str]], list[str]]:
    """全量拉官方目录与成分。返回 (目录名→代码, 代码→{symbol}, symbol→[题材名], 失败题材)。"""
    r = await svc._client.get(f"{svc._base}/api/a-share-index/catalog/ths-index-list", params={"tag": "cn_concept"})
    r.raise_for_status()
    catalog = parse_catalog_items(r.json())
    print(f"官方目录: {len(catalog)} 个概念")

    sem = asyncio.Semaphore(4)
    failed: list[str] = []
    members_by_code: dict[str, set[str]] = {}
    names = {it["name"]: it["code"] for it in catalog}

    async def one(code: str) -> None:
        async with sem:
            for attempt in range(3):
                try:
                    items = await svc.fetch_members(code)
                    members_by_code[code] = {it["symbol"] for it in items}
                    return
                except Exception as exc:  # noqa: BLE001
                    if attempt == 2:
                        failed.append(code)
                        print(f"  FAIL {code}: {exc}")
                    else:
                        await asyncio.sleep(1.0)

    await asyncio.gather(*(one(it["code"]) for it in catalog))
    symbol_themes: dict[str, list[str]] = {}
    for it in catalog:
        for sym in members_by_code.get(it["code"], set()):
            symbol_themes.setdefault(sym, []).append(it["name"])
    return names, members_by_code, symbol_themes, failed


def load_db() -> tuple[dict[str, str], dict[str, set[str]], dict[str, list[str]]]:
    db = sqlite3.connect(DB_PATH)
    c = db.cursor()
    code_by_name = {n: code for code, n in c.execute("select code, name from theme").fetchall()}
    member_by_code: dict[str, set[str]] = {}
    for code, sym in c.execute("select theme_code, symbol from theme_member").fetchall():
        member_by_code.setdefault(code, set()).add(sym)
    sym_themes: dict[str, list[str]] = {}
    for code, sym in c.execute(
        "select m.theme_code, m.symbol from theme_member m join theme t on t.code=m.theme_code"
    ).fetchall():
        name = next((n for n, cc in code_by_name.items() if cc == code), code)
        sym_themes.setdefault(sym, []).append(name)
    db.close()
    return code_by_name, member_by_code, sym_themes


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fix", action="store_true", help="按官方口径修复 theme_member 落库")
    args = ap.parse_args()

    from app.core.db import get_session_factory

    svc = ThemeCatalogService(get_session_factory())
    try:
        names, official_by_code, official_by_symbol, failed = await fetch_all_official(svc)
        code_by_name, db_by_code, db_by_symbol = load_db()

        # ── 归属级 diff ──────────────────────────────────────────────
        added: list[tuple[str, str]] = []   # (题材名, symbol) 实时有库无 → 系统漏标
        removed: list[tuple[str, str]] = [] # 库有实时无 → 系统过期
        for name, code in names.items():
            off = official_by_code.get(code, set())
            cur = db_by_code.get(code, set())
            added.extend((name, s) for s in off - cur)
            removed.extend((name, s) for s in cur - off)

        # ── 个股视角（覆盖系统关心的股票：有任一侧标注的并集）────────
        focus_symbols = sorted(set(official_by_symbol) | set(db_by_symbol))
        per_stock = []
        for sym in focus_symbols:
            ours = sorted(db_by_symbol.get(sym, []))
            ths = sorted(official_by_symbol.get(sym, []))
            if ours != ths:
                per_stock.append((sym, ours, ths))

        print(f"\n===== 官方基准比对（{len(focus_symbols)} 只有标注的个股）=====")
        print(f"归属级漏标（实时有库无）: {len(added)} 条")
        print(f"归属级过期（库有实时无）: {len(removed)} 条")
        print(f"个股标注与官方不一致: {len(per_stock)} 只 / {len(focus_symbols)} 只")

        # ── 涨停池归因三方校验（最近交易日，经本地后端 API 拉涨停池）──
        conflicts: list[dict] = []
        unknown: dict[str, int] = {}
        attr_checked = 0
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=30) as client:
                resp = await client.get("http://127.0.0.1:8000/api/limit-up")
                pool = (resp.json().get("data") or {}).get("pool") or []
            if pool:
                td = pool[0].get("trade_date", "?")
                print(f"\n===== 涨停归因校验（{td}，{len(pool)} 只）=====")
                for row in pool:
                    sym = row.get("symbol", "")
                    reason = row.get("reason") or ""
                    tags = [t.strip() for t in reason.split("+") if t.strip()]
                    for tag in tags:
                        code = names.get(tag)
                        if code is None:
                            unknown[tag] = unknown.get(tag, 0) + 1
                            continue
                        attr_checked += 1
                        if sym not in official_by_code.get(code, set()):
                            conflicts.append(
                                {"symbol": sym, "name": row.get("name", ""), "theme": tag,
                                 "in_official": False, "reason": reason}
                            )
                print(f"校验对 {attr_checked}，归因股不在官方成分 {len(conflicts)}，未知题材 {len(unknown)}")
        except Exception as exc:  # noqa: BLE001
            print(f"涨停池拉取失败（后端未启动?）: {exc}")

        # ── 修复 ────────────────────────────────────────────────────
        if args.fix:
            print("\n开始按官方口径全量修复 theme_member …")
            done = 0
            total_in = 0
            sem = asyncio.Semaphore(4)

            async def fix_one(code: str) -> None:
                nonlocal done, total_in
                async with sem:
                    try:
                        n = await svc.sync_members(code)
                        done += 1
                        total_in += n
                    except Exception as exc:  # noqa: BLE001
                        print(f"  修复失败 {code}: {exc}")

            # names: {题材名: 官方代码} —— 必须迭代 values（官方代码），迭代 key 会把中文题材名传给 API
            await asyncio.gather(*(fix_one(code) for code in names.values()))
            print(f"修复完成: {done}/{len(names)} 题材已按官方成分重写，累计成分 {total_in} 条")
            # 修复后复测
            _, db2_by_code, db2_by_symbol = load_db()
            still = sum(1 for sym in focus_symbols if sorted(db2_by_symbol.get(sym, [])) != sorted(official_by_symbol.get(sym, [])))
            print(f"修复后个股不一致: {still} 只（修复前 {len(per_stock)}）")
            total_m = sqlite3.connect(DB_PATH).execute("select count(*) from theme_member").fetchone()[0]
            empty = sqlite3.connect(DB_PATH).execute(
                "select count(*) from theme t where not exists(select 1 from theme_member m where m.theme_code=t.code)"
            ).fetchone()[0]
            print(f"修复后成分映射 {total_m} 条，空题材 {empty}/{len(names)}")

        # ── 报告落盘 ────────────────────────────────────────────────
        out = ROOT / "docs" / f"theme-audit-{date.today().isoformat()}.md"
        lines = [
            f"# 题材概念标注审计（{date.today().isoformat()}）",
            "",
            f"- 基准：同花顺官方概念目录 {len(names)} 个 + 实时成分（本脚本逐题材实测）",
            f"- 系统库：theme_member {sum(len(v) for v in db_by_code.values())} 条，覆盖 {len(db_by_symbol)} 股",
            f"- 拉取失败题材：{failed or '无'}",
            "",
            "## 准确率统计",
            "",
            "| 指标 | 数值 |",
            "|---|---|",
            f"| 有标注个股总数 | {len(focus_symbols)} |",
            f"| 标注与官方完全一致 | {len(focus_symbols) - len(per_stock)} |",
            f"| 标注不一致 | {len(per_stock)}（{len(per_stock) / max(len(focus_symbols), 1) * 100:.1f}%） |",
            f"| 归属级漏标 / 过期 | {len(added)} / {len(removed)} |",
            f"| 涨停归因校验对 | {attr_checked} |",
            f"| 归因股不在官方成分 | {len(conflicts)} |",
            f"| 归因题材不在官方目录 | {len(unknown)} |",
            "",
            "## 需修正个股明细（股票 / 我们当前标注 / 同花顺标注）",
            "",
            "| 代码 | 我们的标注（库内） | 同花顺官方标注 | 修正动作 |",
            "|---|---|---|---|",
        ]
        for sym, ours, ths in per_stock[:200]:
            action = "补全缺失归属" if not ours else "按官方重写"
            lines.append(f"| {sym} | {'、'.join(ours) or '（无）'} | {'、'.join(ths) or '（无）'} | {action} |")
        if len(per_stock) > 200:
            lines.append(f"| … | 其余 {len(per_stock) - 200} 只见 JSON | | |")
        lines += ["", "## 涨停归因不一致（归因题材官方成分不含该股）", ""]
        if conflicts:
            lines += ["| 代码 | 名称 | 归因题材 | 涨停原因 |", "|---|---|---|---|"]
            lines += [f"| {x['symbol']} | {x['name']} | {x['theme']} | {x['reason'][:40]} |" for x in conflicts[:80]]
        else:
            lines.append("（无）")
        lines += ["", "## 归因题材不在官方目录", ""]
        lines += [f"- {k} ×{v}" for k, v in unknown.items()] or ["（无）"]
        out.write_text("\n".join(lines), encoding="utf-8")
        (out.with_suffix(".json")).write_text(
            json.dumps(
                {"added": added, "removed": removed, "per_stock": per_stock,
                 "conflicts": conflicts, "unknown_themes": unknown, "failed": failed},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\n报告: {out}")
    finally:
        await svc.aclose()


if __name__ == "__main__":
    asyncio.run(main())
