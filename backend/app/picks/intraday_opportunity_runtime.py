"""Shared intraday opportunity assembly + point-in-time evidence runtime.

Business assembly lives here so HTTP reads and backend evidence collection consume the
same implementation.  No scheduler calls an API route or fabricates a Request object.
"""
from __future__ import annotations

import contextlib
import logging
from copy import deepcopy
from datetime import datetime
from pathlib import Path

from app.core.bjtime import beijing_now, to_beijing, to_beijing_naive
from app.core.ttl_cache import cache_on
from app.services.market_snapshot import load_snapshot_map

log = logging.getLogger(__name__)

def _state(holder):
    # HTTP route may pass Request; schedulers pass FastAPI app; tests/services may
    # pass app.state directly. Normalize all three without making business code
    # depend on a Request type.
    if hasattr(holder, "app"):
        holder = holder.app
    return holder.state if hasattr(holder, "state") else holder


def snapshot_context(app) -> tuple[dict[str, dict], str, str | None]:
    """当前全市场快照 + freshness state + 版本时点。"""
    state = _state(app)
    out: dict[str, dict] = {}
    try:
        svc = state.snapshot_service
        version_fn = getattr(svc, "versioned_snapshot", None)
        if callable(version_fn):
            rows, version_as_of = version_fn()
        else:
            rows = [dict(row) for row in (getattr(svc, "snapshot", None) or [])]
            version_as_of = getattr(svc, "last_success", None)
        for row in rows:
            if row.get("symbol"):
                out[str(row["symbol"])] = dict(row)
        fn = getattr(svc, "freshness", None)
        fresh = fn() if callable(fn) else None
        state = getattr(fresh, "state", None) or "unknown"
        as_of = version_as_of or getattr(fresh, "as_of", None) or getattr(svc, "last_success", None)
        return out, state, as_of.isoformat() if hasattr(as_of, "isoformat") else (str(as_of) if as_of else None)
    except Exception:  # noqa: BLE001
        return out, "unknown", None


def snapshot_by(app) -> dict[str, dict]:
    return snapshot_context(app)[0]


def durable_snapshot_context(app) -> tuple[dict[str, dict], str, str] | None:
    """Return the exact durable snapshot version that advanced ``saved_files``."""
    state = _state(app)
    svc = getattr(state, "snapshot_service", None)
    path = getattr(svc, "last_saved_path", None) if svc is not None else None
    raw_as_of = getattr(svc, "last_saved_as_of", None) if svc is not None else None
    if not path or not isinstance(raw_as_of, datetime):
        return None

    from app.services.parquet_store import read_parquet_safe

    df, error = read_parquet_safe(Path(path))
    if df is None:
        raise RuntimeError(f"durable market snapshot unreadable: {path} | {error}")
    snap_by: dict[str, dict] = {}
    for row in df.to_dicts():
        symbol = row.get("symbol")
        if symbol is not None:
            snap_by[str(symbol).zfill(6)] = dict(row)
    if not snap_by:
        raise RuntimeError(f"durable market snapshot empty: {path}")
    snapshot_state = getattr(svc, "last_saved_state", None) or "unknown"
    return snap_by, snapshot_state, raw_as_of.isoformat()


def snapshot_as_of(app) -> datetime | None:
    """Snapshot fact time in Beijing-naive storage semantics.

    Evidence run identity must follow the market snapshot that fed the decision, not
    the wall-clock time of whichever HTTP request or scheduler happened to consume it.
    """
    state = _state(app)
    try:
        svc = state.snapshot_service
        fn = getattr(svc, "freshness", None)
        fresh = fn() if callable(fn) else None
        raw = getattr(fresh, "as_of", None) or getattr(svc, "last_success", None)
        if isinstance(raw, datetime):
            return to_beijing_naive(raw)
        if raw:
            return to_beijing_naive(datetime.fromisoformat(str(raw)))
    except Exception:  # noqa: BLE001 -- missing/invalid version stays explicit fallback below
        return None
    return None


def _ever_sealed_symbols(board: dict) -> set[str]:
    """从未裁剪的题材板提取“今日曾封板”身份，禁止从 UI 展示配额反推。"""
    return {
        str(stock.get("symbol"))
        for theme in (board.get("themes") or [])
        for stock in (theme.get("ladder") or [])
        if stock.get("symbol")
    }


def attach_risk_to_themes(data: dict, snap_by: dict[str, dict]) -> None:
    """题材手风琴路径（/intraday-opportunities）的个股补现价/止损/出场。

    与 /intraday-top 共用 ``attach_risk_fields`` 同一份实现——此前该项补全只写在
    intraday-top 路由里，于是同一张选股卡片（PickCard）在两条数据源上字段丰度不同
    （2026-09-10 用户反馈「其他板块打开的字段不一样」）。
    """
    from app.picks.intraday_opportunity import attach_risk_fields

    for th in data.get("themes") or []:
        attach_risk_fields(th.get("stocks") or [], snap_by)
        # 2026-09-15：候选组（participants）同样要补——否则"可参与候选"反而没有
        # 现价/止损/出场，而"仅参考"的梯队有，字段丰度倒挂
        attach_risk_fields(th.get("participants") or [], snap_by)


async def build_opportunities(
    app, trade_date, top_themes: int, stocks_per_theme: int, *,
    snapshot_bundle: tuple[dict[str, dict], str, str | None] | None = None,
) -> dict:
    """opportunities payload 构建（两处端点共用：全量视图 + 盘中 top 筛选）。

    读 Parquet 是同步阻塞，丢线程池（market.themes 同款处理，曾卡死事件循环）；
    结果缓存 60s（cache key 含参数，两端点同 key 命中同一份）。
    """
    state = _state(app)
    cache = cache_on(state, "picks.opportunities", 60, maxsize=4)
    if snapshot_bundle is None:
        snapshot_bundle = snapshot_context(app)
    snap_by, snapshot_state, snapshot_version = snapshot_bundle
    key = (trade_date, top_themes, stocks_per_theme, snapshot_state, snapshot_version)
    _, cached = await cache.get_or_set(
        key,
        lambda: _build_opportunities_uncached(
            app, trade_date, top_themes, stocks_per_theme,
            snapshot_bundle=snapshot_bundle,
        ),
    )
    # 缓存只保存不可变的装配基线。风险字段取请求时的实时快照，必须写在副本上：
    # 旧实现把缓存对象原地修改，两条并发端点会共享/覆盖同一棵 dict，放大数量与字段抖动。
    payload = deepcopy(cached)
    attach_risk_to_themes(payload["data"], snapshot_by(app))
    return payload


async def _build_opportunities_uncached(
    app, trade_date, top_themes: int, stocks_per_theme: int, *,
    snapshot_bundle: tuple[dict[str, dict], str, str | None] | None = None,
) -> dict:
    """构建一份可缓存的机会基线；一整轮只能消费同一份行情事实。

    ``snapshot_bundle`` 由 ``build_opportunities`` 在缓存 key 生成时一次捕获。
    重型 provider/theme IO 期间即使 MarketSnapshotService 再刷新，也不能让
    participant/tradability/archive 改吃更新后的 B 版本，否则一条 run 会拼接两个时点。
    """
    import asyncio

    from app.picks.intraday_opportunity import assemble
    from app.services.theme_service import _pick_provider, build_theme_board

    state = _state(app)
    hub = state.hub
    if snapshot_bundle is None:
        snapshot_bundle = snapshot_context(app)
    snap_by, snapshot_state, snapshot_as_of_text = snapshot_bundle

    # Premium/theme-board only needs ``change_pct``. Prefer the exact frozen in-memory
    # snapshot used by candidate linkage. Parquet remains cold-start fallback only;
    # missing live facts stay visibly degraded rather than silently mixing versions.
    snapshot_map = snap_by or await asyncio.to_thread(
        load_snapshot_map, state.snapshot_service, trade_date
    )
    board = await build_theme_board(hub.provider, trade_date, snapshot_map=snapshot_map)

    hot_rows: list[dict] = []
    hot_available = False
    ths = _pick_provider(hub.provider, "ThsFuyaoProvider")
    if ths is not None:
        try:
            hot_rows = (await ths.get_hot_stock_list("day"))[:50]
            hot_available = bool(hot_rows)
        except Exception:  # noqa: BLE001 — 人气维度失败不拖垮机会视图，只降级
            log.warning("hot stock list unavailable, distinctiveness degrades to unknown")

    payload = {
        "data": assemble(
            board, hot_rows, hot_available, top_themes=top_themes, stocks_per_theme=stocks_per_theme
        ),
        "meta": {},
    }
    # 簇级官方概念挂靠（09-08「代糖/玉米搜不到」修复）：猎场手风琴与工作台
    # 题材归属由此获得 official_matches（如「功能糖」→官方「代糖概念/玉米」）；
    # 缓存前写入，两端点共用同一份。
    from app.services.official_match import attach_official

    attach_official(app, payload["data"].get("themes") or [])

    # 猎场候选口径（2026-09-15 用户指令）：涨停梯队退居**参考信息**，候选改为
    # 「题材内**当前未封板**、通过候选门槛的联动个股」。容器直接取卡片刚挂好的
    # `catalog_code`（成分重叠挂靠产物）⇒ 页面显示的官方概念与挖掘用的容器必然是
    # 同一个；成分表复用 board_surge 的当日题材倒排缓存（同一份数据，不新建取数）。
    # 挖掘失败**不静默**：写 linkage_note，页面据此显示"本轮无可参与联动候选"。
    try:
        import asyncio as _asyncio

        from app.picks.board_surge import get_index_cache
        from app.picks.intraday_opportunity import attach_participants
        from app.picks.tradability import attach_tradability, index_views

        index, _names = await _asyncio.to_thread(get_index_cache(app).get)
        _sizes, members_by_code = await _asyncio.to_thread(index_views, index)
        themes = payload["data"].get("themes") or []
        # 涨停梯队的 current 状态按**带版本快照**逐只判定；涨停池只提供“今日曾封板”身份。
        # 缺可信时点时保持 unknown，不用首封历史伪造当前仍封或已经开板。
        for th in themes:
            attach_tradability(
                th.get("stocks") or [], snap_by,
                snapshot_state=snapshot_state, snapshot_as_of=snapshot_as_of_text,
            )
        # “曾封板”身份必须来自 build_theme_board 的**完整**当日涨停池梯队，而不是
        # `assemble(top_themes/stocks_per_theme)` 裁剪后的 UI 行；否则没进展示配额的涨停股
        # 会被误当成“从未封板”。board 仍保留全部 theme cards / ladder，assemble 才裁剪。
        ever_sealed = _ever_sealed_symbols(board)
        payload["data"]["linkage_stats"] = attach_participants(
            themes,
            snapshot_by=snap_by,
            ever_sealed_symbols=ever_sealed,
            limit_up_total=(payload["data"].get("summary") or {}).get("limit_up_total"),
            members_by_code=members_by_code,
            snapshot_state=snapshot_state,
            snapshot_as_of=snapshot_as_of_text,
        )

        # 板块权限拆分（用户 2026-09-15：「创业板的不进，只有主板的权限现在」）：
        # ⚠️ **必须在 `sealed`（涨停池全量）与联动挖掘之后**才拆展示面 —— 若先拆，
        # 非主板涨停股就会丢失“曾封板”身份，污染 current-state 重评（实测风险点）。
        # 拆出来的票不是删掉，而是计数留痕：页面要能解释"为什么梯队只剩 3 只"。
        board_excluded = 0
        for th in themes:
            stocks = th.get("stocks") or []
            kept = [s for s in stocks if s.get("tradable") is not False]
            board_excluded += len(stocks) - len(kept)
            th["stocks"] = kept
        payload["data"]["board_excluded_reference"] = board_excluded
        payload["data"]["tradable_boards"] = "沪市主板 / 深市主板（含主板 ST）"
    except Exception as exc:  # noqa: BLE001 — 挖掘失败不拖垮机会视图，显式标注
        # exc_info 不可省：本处曾真实吞掉一个 ImportError（把 attach_participants
        # 当成了 tradability 的导出），只留一句"失败"会让人从零重查（2026-09-15）。
        log.warning("theme linkage mining failed: %s", exc, exc_info=True)
        payload["data"]["linkage_note"] = (
            f"题材联动挖掘失败（{type(exc).__name__}）——本轮无可参与联动候选"
        )

    # RSH-026 S1：在叠加请求级字段前归档 point-in-time 决策链。归档失败不把
    # 行情接口拖死，但必须把 degraded 状态写进响应，不能静默声称“可回放”。
    try:
        from app.picks.opportunity_learning import archive_intraday_pipeline

        evidence = await asyncio.to_thread(
            lambda: archive_intraday_pipeline(
                payload["data"],
                trade_date=trade_date.isoformat() if hasattr(trade_date, "isoformat") else str(trade_date),
                as_of=(
                    to_beijing_naive(datetime.fromisoformat(snapshot_as_of_text))
                    if snapshot_as_of_text else to_beijing_naive(beijing_now())
                ),
            )
        )
        payload["data"]["decision_evidence"] = {
            "state": "ready", "run_id": evidence["run_id"], "records": evidence["records"]
        }
    except Exception as exc:  # noqa: BLE001 — 显式降级；候选本身仍可展示
        log.exception("opportunity evidence archive failed")
        payload["data"]["decision_evidence"] = {
            "state": "degraded", "run_id": None,
            "reason": f"证据归档失败（{type(exc).__name__}）",
        }
    finally:
        for theme in payload["data"].get("themes") or []:
            theme.pop("_candidate_audit", None)

    # 猎场批次 A（需求 7）+ 2026-09-09 收紧（用户：跟踪过多且缺乏依据）+ 2026-09-15 口径：
    # 机会候选登记加**量化硬门槛**，避免盲目大面积跟踪——
    #   ① 只在交易时段登记（非交易时段端点被调用不产生台账数据）
    #   ② 候选须满足任一：进入临板区（板性×0.65 起）/ 联动判定=高
    #
    # ⚠️ 2026-09-15 两处**看起来是改动、实际不改准入集合**的替换（留痕，防误读成放宽）：
    #   a) 遍历对象由 `stocks`（曾封板梯队）改为 `participants`（当前未封板的联动候选）。
    #      **旧遍历是空转**：梯队成员在涨停池内 ⇒ `is_sealed` 恒真 ⇒ 全部 continue
    #      （所以这个循环此前几乎从不产出台账行，台账行实际只来自临板雷达）。
    #   b) `cert_high` 由 certainty（封板质量）改为 linkage=="高"。而 linkage 判「高」
    #      的定义里已含 `pct ≥ pre_limit_floor`（见 picks/tradability.linkage_confidence），
    #      ⇒ guard 的第二分支恒不改变判定结果。替换只是让"为什么算高"可读、可追溯。
    #   ⇒ **准入集合 = 「未封板且进入临板区」**，与 KB-DEC-011 逐字一致，未放宽。
    # watcher 确认与买点触发（dispatch_alert 路径）不受此门槛限制（本就是强信号）。
    with contextlib.suppress(Exception):
        # ⚠️ 导入路径修正（2026-09-15，被新增的「函数内导入可解析」守卫抓出）：
        # 原写 `app.market.trading_status`，但该模块只有**个股停牌判定**；
        # `in_trading_window` 在 `app.market.trade_calendar`（P1-3 收口后的单点，
        # 2026-09-11 的 `9ef498c` 移走了它，此处调用点没跟着改）。
        # 后果：`contextlib.suppress(Exception)` 把 ImportError 吞成静默
        # ⇒ **本段台账登记自写入以来从未执行过**（题材候选一只都没入过册）。
        # 这正是「宽泛 except 让守卫失效」的教科书案例——修的是路径，留住的是教训。
        from app.market.trade_calendar import in_trading_window
        from app.picks.watch_ledger import record_sighting

        now = beijing_now()
        if in_trading_window(now):
            tdate = now.date().isoformat()
            tstamp = now.strftime("%H:%M:%S")
            registered = 0
            for th in payload["data"].get("themes") or []:
                layer = "today_strongest" if th.get("strength_tier") in ("领涨", "强势") else "quiet_starting"
                for s in th.get("participants") or []:
                    if not s.get("symbol"):
                        continue
                    pct = s.get("change_pct")
                    linkage_high = (s.get("linkage") or {}).get("level") == "高"
                    # KB-DEC-011（2026-09-09 用户指令，修订 KB-DEC-008）：涨停前识别才准入——
                    # ① 已封板的候选一律不入册（封板后发现的=迟到；boards≥1 不再是准入条件）；
                    # ② 未封板但未达临板区（板性×0.65）且判定不足——不跟踪
                    from app.picks.pre_limit_radar import board_limit_pct, is_sealed, pre_limit_floor

                    limit_pct = board_limit_pct(str(s["symbol"]), str(s.get("name") or ""))
                    if pct is None or is_sealed(float(pct), limit_pct):
                        continue
                    if float(pct) < pre_limit_floor(limit_pct) and not linkage_high:
                        continue  # 未进临板区且判定不足——不跟踪
                    record_sighting(
                        trade_date=tdate, symbol=str(s["symbol"]),
                        name=str(s.get("name") or ""), layer=layer,
                        source_theme=str(th.get("theme") or ""),
                        reason={
                            "kind": "theme",  # KB-TRADE-13：候选链是题材驱动，登记时点即固化归因
                            "theme": th.get("theme"), "stage": th.get("stage"),
                            "tier": th.get("strength_tier"), "role": s.get("role"),
                            "linkage": s.get("linkage"),
                            "tradability": s.get("tradability"),
                            "gate": f"pre_limit pct={pct} floor={pre_limit_floor(limit_pct)} limit={limit_pct:.0f}cm linkage_high={linkage_high}",
                            "basis": (s.get("basis") or "")[:200],
                        },
                        is_leader=False,  # 未涨停 ⇒ 无梯队角色，不冒充龙头
                        boards=0,
                        entry_price=None,  # 登记时以告警触发价优先；此处无价格由清算兜底
                        entry_time=tstamp,
                    )
                    registered += 1
            if registered:
                log.info("watch ledger: %d candidates registered (gate: 临板区, 未封板——KB-DEC-011)", registered)
    return payload

EVIDENCE_TOP_THEMES = 5
EVIDENCE_STOCKS_PER_THEME = 8


async def archive_intraday_evidence_tick(app) -> dict:
    """Archive one canonical candidate→gate→rank run per durable market snapshot.

    The snapshot service persists at a slower cadence than its in-memory polling.  The
    persisted version is the trigger so evidence does not multiply with UI refreshes.
    A failed archive does not advance the cursor and is retried by the registry.
    """
    from app.market.trade_calendar import in_trading_window
    from app.services.market_snapshot import default_trade_date

    state = _state(app)
    svc = getattr(state, "snapshot_service", None)
    saved_files = int(getattr(svc, "saved_files", 0) or 0) if svc is not None else 0
    last_seen = int(getattr(state, "opportunity_evidence_saved_files", 0) or 0)
    if saved_files <= last_seen:
        return {"state": "idle", "saved_files": saved_files, "reason": "no_new_snapshot"}

    import asyncio

    snapshot_bundle = await asyncio.to_thread(durable_snapshot_context, app)
    if snapshot_bundle is None:
        raise RuntimeError(
            f"new durable snapshot {saved_files} has no exact saved path/as_of metadata"
        )
    _snap_by, _snapshot_state, snapshot_version = snapshot_bundle
    if not snapshot_version:
        raise RuntimeError(f"new durable snapshot {saved_files} has no fact time")
    snapshot_at = to_beijing(datetime.fromisoformat(snapshot_version))
    if not in_trading_window(snapshot_at):
        # Decide from the saved snapshot fact time, not the later consumer clock.
        state.opportunity_evidence_saved_files = saved_files
        return {"state": "skipped", "saved_files": saved_files, "reason": "outside_trading_window"}

    trade_date = await default_trade_date(state.hub)
    if trade_date != snapshot_at.date():
        raise RuntimeError(
            f"new durable snapshot {saved_files} trade_date mismatch: "
            f"{trade_date} != {snapshot_at.date()}"
        )

    payload = await build_opportunities(
        app, trade_date, EVIDENCE_TOP_THEMES, EVIDENCE_STOCKS_PER_THEME,
        snapshot_bundle=snapshot_bundle,
    )
    evidence = (payload.get("data") or {}).get("decision_evidence") or {}
    if evidence.get("state") != "ready":
        raise RuntimeError(
            "intraday opportunity evidence archive not ready: "
            + str(evidence.get("reason") or evidence.get("state") or "unknown")
        )
    state.opportunity_evidence_saved_files = saved_files
    log.info(
        "intraday opportunity evidence archived: snapshot=%s run=%s records=%s",
        saved_files, evidence.get("run_id"), evidence.get("records"),
    )
    return {
        "state": "archived", "saved_files": saved_files,
        "run_id": evidence.get("run_id"), "records": evidence.get("records"),
    }
