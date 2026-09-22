"""B5 迁移三态机测试（临时 sqlite 文件库，不碰真实 ashare.db）。

db 文件一律放 pytest tmp_path（CI 干净 checkout 无 backend/data 目录，
SQLite 不会自建父目录——曾因此 Linux CI 全红而 macOS 本地全绿）。
"""

from __future__ import annotations

import ast
import sqlite3
from pathlib import Path

from sqlalchemy import create_engine, inspect, text

from app.core.migrations import run_migrations

#: 迁移脚本目录（`tests/test_db_migrations.py` → `parents[1]` = `backend/`）。
MIGRATIONS_VERSIONS_DIR = Path(__file__).resolve().parents[1] / "migrations" / "versions"


def _fresh_engine(tmp_path: Path, tmp_name: str):
    path = tmp_path / f"tmp-mig-{tmp_name}.db"
    if path.exists():
        path.unlink()
    return create_engine(f"sqlite:///{path}"), path


def _tables(engine) -> set[str]:
    insp = inspect(engine)
    return {t for t in insp.get_table_names() if t != "sqlite_sequence"}


def test_fresh_database_created_at_baseline(tmp_path):
    engine, path = _fresh_engine(tmp_path, "fresh")
    try:
        action = run_migrations(engine)
        assert action == "created"
        tables = _tables(engine)
        assert {
            "watchlist", "paper_account", "review_reports", "prediction_themes",
            "minute_decisions", "opportunity_decision_snapshot",
            "opportunity_outcome_label", "opportunity_outcome_revision", "alembic_version",
        } <= tables
        with engine.connect() as conn:
            ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert ver, "alembic_version 必须已写入"
        # excess_pct 必须 nullable（迁移 f6b2c8e4a9d3）：基准缺失 None 不得被固化成 0
        insp = inspect(engine)
        col = next(c for c in insp.get_columns("daily_pick_review") if c["name"] == "excess_pct")
        assert col["nullable"], "excess_pct 迁移后必须 nullable"
        # BUG-014（2026-09-16）回归：这 5 张表曾经**不在全新库里**——它们的迁移用
        # `get_engine()`（**settings 默认库**的 engine）建表，于是表落到了 data/ashare.db，
        # 而迁移目标库里一张都没有。生产库"碰巧建对"（默认库正是它）⇒ 长期未暴露。
        # 这条断言就是该缺陷的直接回归：缺表即红（修复前实测确实缺这 5 张）。
        missing = {
            "theme", "theme_member", "theme_override",
            "real_trade", "real_position_override",
        } - tables
        assert not missing, (
            f"全新库缺 {sorted(missing)}：这些表的迁移把表建到了默认库而非迁移目标库"
            "（BUG-014；见 core/migrations.py 的「不会把表建到别处」承诺）"
        )
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def test_legacy_database_stamped_and_completed(tmp_path):
    """存量库（只有一张旧表、无 alembic_version）→ create_all 补齐 + stamp。"""
    engine, path = _fresh_engine(tmp_path, "legacy")
    try:
        with engine.begin() as conn:
            # 模拟最老的存量库：只有 watchlist 一张表
            conn.execute(text("CREATE TABLE watchlist (id INTEGER PRIMARY KEY, symbol VARCHAR(12))"))
        action = run_migrations(engine)
        assert action.startswith("stamped")
        tables = _tables(engine)
        assert {"paper_account", "review_reports", "prediction_themes"} <= tables, "缺失表必须被 create_all 补齐"
        with engine.connect() as conn:
            ver = conn.execute(text("SELECT version_num FROM alembic_version")).scalar()
        assert ver
        # 存量的旧表数据结构未被破坏（可查）
        with engine.connect() as conn:
            conn.execute(text("SELECT symbol FROM watchlist"))
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


def test_versioned_database_upgrades_idempotently(tmp_path):
    """已版本化库重复 run → upgrade 幂等（无变更时 no-op）。"""
    engine, path = _fresh_engine(tmp_path, "versioned")
    try:
        assert run_migrations(engine) == "created"
        # 幂等：再跑一次仍是 upgrade 路径且不报错
        action = run_migrations(engine)
        assert action == "upgraded"
        assert "alembic_version" in _tables(engine)
        # sqlite 直查校验 alembic_version 未被破坏
        conn = sqlite3.connect(path)
        assert conn.execute("SELECT COUNT(*) FROM alembic_version").fetchone()[0] == 1
        conn.close()
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------- 模型 ⇄ 迁移 schema 一致性
#
# **为什么需要这条**：schema 有**两套真相源**——模型（`Base.metadata`）与迁移脚本，
# 而两者的消费路径不同：测试库走 `Base.metadata.create_all`（见 `tests/conftest.py`），
# 生产库走 alembic。两者一旦不一致，就会出现「测试全绿、线上形状不同」——
# 2026-09-16 实测命中一处：`opportunity_decision_snapshot.snapshot_id` 在模型里是
# `unique=True, index=True`（唯一索引），而迁移写成表级 `UniqueConstraint` + **非唯一**索引
# ⇒ 唯一性虽都成立，但 schema 形状（索引是否唯一 / 约束条数）已经分叉，
# `create_all` 兜底分支建出的库与 alembic 建出的库不是同一种结构。
#
# ⚠️ **本条只覆盖 RSH-026 新建的两张表**，不是「全库已一致」。同轮机械摸底：
# **27 张公共表里 11 张存在偏差**（`agent_*` 系列的 nullable 差异、`watchlist_group` /
# `paper_position` 的索引名与约束差异等，均属存量、非本轮引入）⇒ 全库清账是独立任务
# （账本 `GOV-013`）。**在此处收窄范围是刻意的**：把已知存量偏差一并断红只会让门禁长期失效。
# 新增表时把模型类追加进下面的元组即可获得同样的保护。
_MODELS_UNDER_PARITY = (
    "OpportunityDecisionSnapshot", "OpportunityDecisionRun", "OpportunityOutcomeLabel",
    "OpportunityOutcomeRevision", "AgentParamPromotionApproval", "AgentResourceUsage",
)


def _schema_drift(engine, model) -> list[str]:
    """逐列/逐索引/逐唯一约束比对 DB 与模型；返回差异描述（空 = 一致）。"""
    table = model.__tablename__
    insp = inspect(engine)
    problems: list[str] = []
    db_cols = {c["name"]: c for c in insp.get_columns(table)}
    model_cols = {c.name for c in model.__table__.columns}
    if set(db_cols) != model_cols:
        problems.append(
            f"列集合 缺={sorted(model_cols - set(db_cols))} 多={sorted(set(db_cols) - model_cols)}"
        )
    nullable = [
        c.name for c in model.__table__.columns
        if c.name in db_cols and bool(c.nullable) != bool(db_cols[c.name]["nullable"])
    ]
    if nullable:
        problems.append(f"nullable 不一致 {sorted(nullable)}")
    db_idx = {i["name"]: bool(i.get("unique")) for i in insp.get_indexes(table)}
    model_idx = {i.name: bool(i.unique) for i in model.__table__.indexes}
    if db_idx != model_idx:
        problems.append(f"索引不一致 db={db_idx} model={model_idx}")
    db_uq = sorted(tuple(sorted(u["column_names"])) for u in insp.get_unique_constraints(table))
    model_uq = sorted(
        tuple(sorted(c.name for c in con.columns))
        for con in model.__table__.constraints if con.__class__.__name__ == "UniqueConstraint"
    )
    if db_uq != model_uq:
        problems.append(f"唯一约束不一致 db={db_uq} model={model_uq}")
    return problems


def test_opportunity_learning_tables_match_their_models(tmp_path):
    """新增表在 alembic 与 create_all 两条路径下必须是**同一种结构**。"""
    from app.models.opportunity_learning import (
        OpportunityDecisionSnapshot,
        OpportunityDecisionRun,
        OpportunityOutcomeLabel,
        OpportunityOutcomeRevision,
    )
    from app.models.agent import AgentParamPromotionApproval, AgentResourceUsage

    models = {
        "OpportunityDecisionSnapshot": OpportunityDecisionSnapshot,
        "OpportunityDecisionRun": OpportunityDecisionRun,
        "OpportunityOutcomeLabel": OpportunityOutcomeLabel,
        "OpportunityOutcomeRevision": OpportunityOutcomeRevision,
        "AgentParamPromotionApproval": AgentParamPromotionApproval,
        "AgentResourceUsage": AgentResourceUsage,
    }
    engine, path = _fresh_engine(tmp_path, "parity")
    try:
        assert run_migrations(engine) == "created"
        for name in _MODELS_UNDER_PARITY:
            model = models[name]
            problems = _schema_drift(engine, model)
            assert not problems, f"{model.__tablename__} 迁移与模型不一致：{problems}"
        # 判据自证（防本用例退化成恒真）：唯一性到底由谁承担必须被钉住——
        # 2026-09-16 实测的真实形态是「表级 UniqueConstraint + 非唯一索引」，
        # 若改写成唯一索引（`unique=True, index=True`）即与**已应用的生产迁移**分叉。
        insp = inspect(engine)
        idx = {i["name"]: i for i in insp.get_indexes("opportunity_decision_snapshot")}
        assert not idx["ix_opportunity_decision_snapshot_snapshot_id"]["unique"], (
            "snapshot_id 的索引必须保持非唯一：唯一性由表级 UniqueConstraint 承担"
            "（与已应用的生产迁移 7d4e2c9a6b1f 一致）"
        )
        uq = [tuple(sorted(u["column_names"])) for u in insp.get_unique_constraints("opportunity_decision_snapshot")]
        assert ("snapshot_id",) in uq, "snapshot_id 的唯一性必须真的存在，不能被悄悄丢掉"

        task_cols = {c["name"]: c for c in insp.get_columns("agent_task")}
        assert "cancel_requested_at" in task_cols and task_cols["cancel_requested_at"]["nullable"] is True
        task_idx = {i["name"] for i in insp.get_indexes("agent_task")}
        assert "ix_agent_task_cancel_requested_at" in task_idx
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)


# ---------------------------------------------------------------- BUG-014 根因守卫


def test_migration_scripts_never_use_the_default_engine():
    """迁移脚本一律走 `op.get_bind()`，**禁止** `get_engine()`（BUG-014 根因守卫）。

    **为什么需要（判据要打在根因上）**：`app/core/db.get_engine()` 返回的是 settings
    **默认库**的 engine（本仓 = `data/ashare.db`），而 `run_migrations(engine)` 的契约是
    「**在传入的 engine 这条连接上**建全库」（见 `app/core/migrations.py` 模块 docstring：
    "迁移全程在传入 engine 的连接上执行……**不会把表建到别处**"）。两者混用 ⇒
    ① 迁移目标库里**少表**（换 `DATABASE_URL` 的新环境 / 临时库 / 冷启动重建直接炸）；
    ② 跑 `run_migrations(临时 engine)` 会**顺带往默认库写表**（副作用漏到生产库）；
    ③ **门禁恒绿**——没有任何断言在查这些表是否存在，所以没人会发现。

    **同族已发生 4 次**（正是"只加注释不加机器守卫"的代价）：
      `e7a2b9c4d1f8`（daily_picks）· `9c4d7e2a1b3f`（event）—— 2026-09-08/09 修，**仅留注释**；
      `6f2ab91c4d70`（theme）· `c8d51f2e9a4b`（real_position）—— 2026-09-16 复发，本守卫落地点。
    故本用例的判据面是**全部迁移脚本**（不是那 5 张表），这样"下一次"也拦得住。

    **为什么必须用 AST 而不是正则**：本仓 4 个迁移文件的**解释性注释里字面写着
    `get_engine()`**（就是在说明"为什么不能这么写"）⇒ 字符串/正则扫描会把这 4 处
    **误判为违规**，守卫一上线就红、随后必然被人放宽掉。AST 只看**真实调用节点**。
    """
    files = sorted(MIGRATIONS_VERSIONS_DIR.glob("*.py"))
    # 判据自证（防"覆盖面静默失效"）：扫描面必须是真实迁移目录，
    # 否则目录改名/路径写错会让本用例退化成恒真（[[KB-ENG-72]] 同族口径）。
    assert len(files) > 20, (
        f"迁移脚本扫描面异常：{MIGRATIONS_VERSIONS_DIR} 下仅 {len(files)} 个 .py ⇒ "
        "路径可能已变，本守卫会静默恒绿"
    )

    hits: list[str] = []
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else None
            )
            if name == "get_engine":
                hits.append(f"{path.name}:{node.lineno}")

    assert not hits, (
        "迁移脚本不得使用 get_engine()（会把表建到默认库而非迁移目标库）："
        f"{hits} ⇒ 改用 op.get_bind()（详见 app/core/migrations.py docstring）"
    )


def test_imp052_rollout_backfills_same_day_legacy_agent_consumption(tmp_path):
    """IMP-052 migration must not reset today's model/task quotas on deploy/restart."""
    import json
    from alembic import command
    from alembic.config import Config
    from app.core.migrations import BACKEND_DIR

    engine, path = _fresh_engine(tmp_path, "imp052-rollout")
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.attributes["configure_logger"] = False
    try:
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "f4a2c8e1b6d3")
            day = conn.scalar(text("select date(datetime('now','+8 hours'))"))
            stamp = f"{day} 10:00:00"
            conn.execute(text("""
                insert into agent_audit(actor,action,target,before,after,task_id,rollback_ref,at)
                values
                  ('ai','agenda.generate','evolution',null,null,'task-agenda',null,:at),
                  ('ai','code.propose','proposal-x',null,null,'task-code',null,:at)
            """), {"at": stamp})
            items = json.dumps([
                {"class": "A", "status": "executed", "finding": "a"},
                {"class": "B", "status": "executed", "finding": "b"},
                {"class": "B", "origin": "data_health", "status": "executed", "finding": "health"},
                {"class": "C", "status": "rejected", "finding": "c"},
            ], ensure_ascii=False)
            conn.execute(text("""
                insert into agent_agenda(date,status,inputs,items,budget,error,created_at,finished_at)
                values (:day,'executed','{}',:items,'{}',null,:at,:at)
            """), {"day": day, "items": items, "at": stamp})

        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.upgrade(cfg, "b5c9e7a2d4f1")

        with engine.connect() as conn:
            rows = conn.execute(text("""
                select scope,slot,kind,purpose,state,usage_known,task_id
                from agent_resource_usage
                order by scope,slot
            """)).mappings().all()
            by_scope: dict[str, list[dict]] = {}
            for row in rows:
                by_scope.setdefault(str(row["scope"]), []).append(dict(row))

            assert len(by_scope["autonomy_llm"]) == 2
            assert {r["purpose"] for r in by_scope["autonomy_llm"]} == {"agenda.generate", "code.propose"}
            assert all(r["state"] == "unknown" and r["usage_known"] == 0 for r in by_scope["autonomy_llm"])
            assert [r["slot"] for r in by_scope["autonomy_llm"]] == [1, 2]

            assert len(by_scope["code_proposal"]) == 1
            assert by_scope["code_proposal"][0]["purpose"] == "code.propose"
            assert by_scope["code_proposal"][0]["slot"] == 1

            assert len(by_scope["autonomy_task"]) == 2
            assert {r["purpose"] for r in by_scope["autonomy_task"]} == {"agenda.A", "agenda.B"}
            assert [r["slot"] for r in by_scope["autonomy_task"]] == [1, 2]
            assert not any(r["purpose"] == "agenda.C" for r in by_scope["autonomy_task"])

            assert conn.scalar(text("select version_num from alembic_version")) == "b5c9e7a2d4f1"
            assert conn.scalar(text("pragma integrity_check")) == "ok"
    finally:
        engine.dispose()
        path.unlink(missing_ok=True)
