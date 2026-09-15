"""应用启动装配（一）：**服务构建**。

从 `app/main.py` 的 `lifespan` 中切出（`IMP-027` 装配重构，2026-09-15）。
搬家原则是**零行为变化**，因此本模块刻意保留了原实现的三条隐性契约：

1. **`app.state.<name>` 的赋值顺序即语义**。`risk_engine` 必须先于 `paper` 构造
   （`§6.5b #2` 起 `check_order` 接入模拟撮合硬拦截）；`paper_shadow` 是否装配
   决定 `picks-shadow` 调度器的 `enabled`。顺序照抄，不重排。
2. **延迟导入保持延迟**。诸如 `from app.picks.shadow import ShadowRunner` 原本写在
   函数体内；提到模块级会改变加载期依赖图，可能**引入新的 import 环**
   （`.workbuddy/tools/py-import-cycle-detect.py` 可核验）。一律维持函数内导入。
3. **失败降级形态不变**：题材目录未配 key 时 `theme_catalog = None`（端点 503，
   其余功能不受影响）；agent 运行时对账与参数覆盖层恢复允许失败
   （`contextlib.suppress`）⇒ 不得改成会抛的写法。

⚠️ 表注册（`Base.metadata`）**不在这里**：`main.py` 顶部的模型导入与
`_REGISTERED_MODELS` 是「导入副作用」声明，与 app 对象同层，留在原处。
"""
from __future__ import annotations

import contextlib
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from app.core.config import settings
from app.core.db import get_engine, get_session_factory
from app.data_providers import build_provider
from app.events.store import EventStore
from app.market.alert_engine import AlertEngine
from app.paper.engine import PaperTradingEngine
from app.repositories.alert_repo import AlertRepository
from app.repositories.watchlist_repo import WatchlistRepository
from app.review.service import ReviewService
from app.risk.engine import RiskEngine
from app.services.quote_hub import QuoteHub
from app.services.snapshot_service import MarketSnapshotService
from app.services.theme_catalog_service import ThemeCatalogService

log = logging.getLogger(__name__)


@dataclass
class AppServices:
    """`lifespan` 期构建的进程内服务集合。

    显式容器替代原来的「一大片局部变量」：调度器声明需要其中若干成员，
    用 dataclass 传参可让依赖面**可读**（谁用了什么一眼可见），
    也避免把闭包捕获的变量改成全局单例（那会引入测试间串扰）。
    """

    provider: Any
    hub: QuoteHub
    watchlist_repo: WatchlistRepository
    alert_repo: AlertRepository
    alert_engine: AlertEngine
    snapshot_service: MarketSnapshotService
    risk_engine: RiskEngine
    paper: PaperTradingEngine
    theme_catalog: ThemeCatalogService | None
    event_store: EventStore
    review: ReviewService

    async def aclose(self) -> None:
        """停机收尾：先关 provider，再关题材目录（两者的连接池都不是无限重试的）。"""
        from app.core.scheduler import SHUTDOWN_GRACE_SECONDS
        import asyncio

        with contextlib.suppress(Exception, TimeoutError):
            await asyncio.wait_for(self.provider.aclose(), timeout=SHUTDOWN_GRACE_SECONDS)
        if self.theme_catalog is not None:
            with contextlib.suppress(Exception, TimeoutError):
                await asyncio.wait_for(
                    self.theme_catalog.aclose(), timeout=SHUTDOWN_GRACE_SECONDS
                )


def build_services(app: FastAPI) -> AppServices:
    """按既有顺序构建全部进程内服务，并写 `app.state`。

    ⚠️ 本函数只做「构造 + 挂 state」。常驻任务的声明在 `schedulers.py`，
    两者分开是为了让"进程里有哪些服务"与"跑着哪些循环"各自可读。
    """
    session_factory = get_session_factory()

    # schema 唯一归 alembic 管（B5）：三态 stamp-or-upgrade 替代裸 create_all
    from app.core.migrations import run_migrations

    migration_action = run_migrations(get_engine())
    log.info("database migration: %s", migration_action)
    repo = WatchlistRepository(session_factory)
    repo.ensure_seeded(settings.watchlist_symbols)

    provider = build_provider(settings)
    hub = QuoteHub(
        provider=provider,
        poll_interval=settings.poll_interval_seconds,
        get_watchlist=repo.list_symbols,
        stale_after=settings.stale_after_seconds,
    )
    app.state.hub = hub
    app.state.watchlist_repo = repo

    # --- 预警引擎（Phase 8）：规则轮询 + 通知通道抽象 ---
    alert_repo = AlertRepository(session_factory)
    alert_engine = AlertEngine(alert_repo, repo, interval=settings.alert_poll_interval_seconds)
    app.state.alert_repo = alert_repo
    app.state.alert_engine = alert_engine

    async def live_quote(symbol: str):
        try:
            from app.data_quality.validator import validate_quote
            from app.services.quote_enrich import fill_limit_prices

            q = await provider.get_quote(symbol)
            if q is None:
                return None
            # ths 快照无涨跌停价：从腾讯源补齐（撮合的涨跌停校验依赖它）。
            # 补全逻辑只有一份（quote_enrich），REST 单只行情端点共用，避免两边口径漂移。
            q = await fill_limit_prices(provider, q)
            return validate_quote(q)
        except Exception:
            return None

    async def hub_trading_days():
        providers = [hub.provider] + (
            hub.provider.providers if hasattr(hub.provider, "providers") else []
        )
        for prov in providers:
            if hasattr(prov, "get_trading_days"):
                try:
                    return await prov.get_trading_days()
                except Exception:
                    continue
        return None

    snapshot_service = MarketSnapshotService(
        poll_interval=settings.snapshot_poll_interval_seconds,
        save_interval=settings.snapshot_save_interval_seconds,
        parquet_dir=Path(settings.parquet_dir),
    )
    app.state.snapshot_service = snapshot_service

    # --- 风险引擎（Phase 5）：市场状态 + 仓位参数 + 订单预检 ---
    # 先于 paper 构造：§6.5b #2（2026-09-13）起 check_order 接入模拟撮合硬拦截，
    # main 账户的买入在下单时经 risk_engine 把关（见 PaperTradingEngine 边界注释）。
    risk_engine = RiskEngine(
        hub=hub,
        snapshot_service=snapshot_service,
        session_factory=session_factory,
        app_state=app.state,  # P1-3：情绪判定并入全站共享 60s 槽
    )
    app.state.risk_engine = risk_engine

    paper = PaperTradingEngine(
        session_factory, live_quote, hub_trading_days, risk_engine=risk_engine
    )
    app.state.paper = paper

    # --- 影子持仓（picks-intraday-fusion-assessment P0-B）：scope=shadow 独立账户 ---
    # 每日精选的 A/B 对照组：晨窗把最新组合按执行闸门模拟执行，验证空仓闸门
    # 机会成本与执行闸门价值。与 main 账户数据完全隔离（scope 列）。
    # ⚠️ 刻意**不注入 risk_engine**：影子账户是研究仪器，风控否决会污染 A/B 口径
    #（上游 gate.py 已各自把关），见 PaperTradingEngine 边界注释。
    paper_shadow = None
    if settings.picks_shadow_enabled:
        paper_shadow = PaperTradingEngine(
            session_factory, live_quote, hub_trading_days, scope="shadow",
        )
        from app.picks.shadow import ShadowRunner

        app.state.paper_shadow = ShadowRunner(paper_shadow, session_factory)

    # --- 题材字典/官方成分（architecture-design §1 T1）：fuyao 官方目录与成分同步 ---
    try:
        theme_catalog = ThemeCatalogService(session_factory)
    except RuntimeError as exc:
        # 未配置 ths key 时降级为 None：题材端点返回 503，其余功能不受影响
        log.warning("theme catalog disabled: %s", exc)
        theme_catalog = None
    app.state.theme_catalog = theme_catalog

    # --- 事件驱动（architecture-design §1 E1）：EventCard 存储/查询 ---
    event_store = EventStore(session_factory)
    app.state.event_store = event_store

    # --- 盘后复盘 Agent：服务实例 + 收盘后调度 ---
    review_svc = ReviewService(
        hub=hub,
        snapshot_service=snapshot_service,
        session_factory=session_factory,
        model=settings.review_model,
        llm_base_url=settings.review_llm_base_url,
        llm_api_key=settings.review_llm_api_key,
        llm_model=settings.review_llm_model,
        llm_provider=settings.llm_provider,
        llm_cli_path=settings.llm_cli_path,
        methodology_version=settings.review_methodology_version,
        state=app.state,
    )
    app.state.review = review_svc

    # AI 控制台运行时（任务执行器需要 state 上的服务）+ 启动对账：
    # 残留 running/queued 任务标为 failed（进程重启=任务已中断，不假装还在跑）
    from app.services.agent_tasks import init_agent_runtime, reconcile_on_startup

    init_agent_runtime(app)
    with contextlib.suppress(Exception):
        interrupted = reconcile_on_startup()
        if interrupted:
            log.warning("agent tasks interrupted by restart: %d 条已标 failed", interrupted)
    # 参数覆盖层恢复（重启后覆盖仍生效——覆盖表是持久层，provider 是进程内注册）
    with contextlib.suppress(Exception):
        from app.services.agent_params import refresh_runtime_overrides

        refresh_runtime_overrides()

    # 告警 AI 判读 worker（P1）：规则触发 → AI 判断是否值得提醒 → 悬浮球
    from app.services.alert_triage import set_app_state as _triage_set_app

    _triage_set_app(app)  # P1-5 响应建议需要 state（题材目录/快照）

    return AppServices(
        provider=provider,
        hub=hub,
        watchlist_repo=repo,
        alert_repo=alert_repo,
        alert_engine=alert_engine,
        snapshot_service=snapshot_service,
        risk_engine=risk_engine,
        paper=paper,
        theme_catalog=theme_catalog,
        event_store=event_store,
        review=review_svc,
    )
