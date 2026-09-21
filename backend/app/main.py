from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.deps import require_api_token
from app.api.routes import agent as agent_route
from app.api.routes import backtest as backtest_route
from app.api.routes import health as health_route
from app.api.routes import market as market_route
from app.api.routes import news as news_route
from app.api.routes import paper as paper_route
from app.api.routes import review as review_route
from app.api.routes import watchlist as watchlist_route
from app.api.routes import alert as alert_route
from app.api.routes import risk as risk_route
from app.api.routes import theme_catalog as theme_catalog_route
from app.api.routes import events as events_route
from app.api.routes import picks as picks_route
from app.api.routes import picks_intraday as picks_intraday_route
from app.api.routes import real_position as real_position_route
from app.api.routes import assistant as assistant_route
from app.api.routes import ext_data as ext_data_route
from app.api.routes import notifications as notifications_route
from app.bootstrap import build_services, register_schedulers
from app.core.auth import validate_auth_posture
from app.core.config import settings
from app.core.scheduler import SchedulerRegistry
from app.models.alert import AlertEvent, AlertRule
from app.models.event import EventCard, EventDirection
from app.models.paper import PaperAccount, PaperOrder, PaperPosition
from app.models.theme_catalog import Theme, ThemeMember, ThemeOverride
from app.predict.models import (  # noqa: F401  注册预判两张表
    PredictionReportRow,
    PredictionThemeRow,
)
from app.review.models import (  # noqa: F401  注册复盘三张表
    ReviewActionItemRow,
    ReviewMetaInsightRow,
    ReviewReportRow,
)
from app.market.sentiment_history import SentimentHistoryRow  # noqa: F401  注册情绪序列表
from app.models.watch_ledger import WatchLedger  # noqa: F401  注册盘中跟踪台账表（猎场批次 A）
from app.models.notification import NotificationReadState  # noqa: F401  注册通知已读状态表（2026-09-12）
from app.models.notification_outbox import NotificationAttempt, NotificationOutbox
from app.models.opportunity_learning import (  # noqa: F401  point-in-time 机会证据与结果标签
    OpportunityDecisionRun,
    OpportunityDecisionSnapshot,
    OpportunityOutcomeLabel,
    OpportunityOutcomeRevision,
)
from app.websocket.routes import router as ws_router

# 显式持有引用：确保各模块的表注册进 Base.metadata，否则 create_all 不会建表
_REGISTERED_MODELS = (
    PaperAccount, PaperOrder, PaperPosition,
    ReviewReportRow, ReviewActionItemRow, ReviewMetaInsightRow,
    PredictionReportRow, PredictionThemeRow,
    SentimentHistoryRow,
    AlertRule, AlertEvent,
    Theme, ThemeMember, ThemeOverride,
    EventCard, EventDirection,
    WatchLedger,
    NotificationReadState,
    NotificationOutbox, NotificationAttempt,
    OpportunityDecisionSnapshot, OpportunityDecisionRun,
    OpportunityOutcomeLabel, OpportunityOutcomeRevision,
)

logging.basicConfig(level=settings.log_level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期：**只保留顺序与生命周期**，装配细节分两层下沉（`IMP-027`）。

    切分（2026-09-15）：原实现把"构建服务"与"声明 25 个常驻循环"混在本函数里，
    合计约 550 行，既读不出"进程里有那些服务"，也读不出"跑着哪些循环"。现在：

    - `bootstrap.services.build_services(app)` —— 构建服务 + 写 `app.state`；
    - `bootstrap.schedulers.register_schedulers(...)` —— 只声明常驻循环。

    本函数因此只做四件事：**构建 → 声明并启动 → 冷启动填充 → 停机收割**。
    ⚠️ 判据：这是纯粹的**搬家**，`app.state` 赋值顺序、调度器声明顺序、
    延迟导入位置与失败降级形态都必须逐条保真（见两个模块各自的 docstring）。
    """
    services = build_services(app)

    # --- 常驻调度注册表（S2-2）：声明 → 启动 → 收割全走**一份**清单 ---
    # 此前是 23 处 create_task 与一份手写停机清单并存，两边都要人工同步；
    # 现在 add() 一次声明，shutdown() 统一收割，并发死亡自愈 + 状态可查
    # （GET /api/system/schedulers）。详见 app/core/scheduler.py 模块 docstring。
    reg = SchedulerRegistry()
    app.state.schedulers = reg
    register_schedulers(reg, app, services)

    # --- 统一拉起（S2-2）：声明完毕，一次启动；此后由注册表负责观测与死亡自愈 ---
    await reg.start()

    try:
        await services.hub.refresh()  # 冷启动立即填充，接口首次调用即有数据
        await services.risk_engine.refresh()
    except Exception:
        log.exception("initial refresh failed; serving stale/empty until next cycle")
    yield
    # --- 停机：统一收割（先发信号、再限时收割，任何单任务不得拖死关机）---
    # 此前这里是与启动清单并列的**第二份手写清单**（26 行 _reap），现在由注册表
    # 按同一份声明收敛：能自己退的走 stop 事件，其余 cancel，逐个限时收割。
    await reg.shutdown()
    await services.aclose()


app = FastAPI(title=settings.app_name, version=settings.version, lifespan=lifespan)

# ── 鉴权姿态校验（R22，fail closed）──────────────────────────────────────────
# 必须在**模块级**调用（即"导入 app 对象"这一步就生效），使 uvicorn / 测试夹具 /
# 脚本直连等**任何**启动路径拿到同一结论——放在 lifespan 里就只有"经 ASGI 启动"
# 这一条路径会被覆盖。校验内容（取值合法性 + shared 必配 token）见 core/auth.py。
validate_auth_posture()

# 统一错误契约：所有错误响应形态 {detail, code}（技术评审 B1）
from app.core.errors import register_error_handlers  # noqa: E402

register_error_handlers(app)


# 性能基线中间件（策略进化 P1 方向4）：逐请求记录「路由模板 → 耗时」，
# /api/system/metrics 聚合 p50/p95/p99。call_next 返回后 scope["route"] 已被
# 路由器写入，拿不到（404/中间链异常）退化为数字折叠的原始路径。
@app.middleware("http")
async def _perf_middleware(request, call_next):
    import time as _time

    from app.core import perf as _perf

    t0 = _time.perf_counter()
    try:
        response = await call_next(request)
    finally:
        route = request.scope.get("route")
        route_path = getattr(route, "path", None) or _perf.collapse_path(
            request.scope.get("path", "")
        )
        _perf.record_api(route_path, (_time.perf_counter() - t0) * 1000)
    return response

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── 路由挂载：默认拒绝（R22 统一鉴权边界）────────────────────────────────────
# `dependencies=_AUTH_GUARD` = 该 router 下**每一条**路由都要求入站凭据。
# 只有 `/api/health`（存活探针）例外，且是**结构性**例外：它不挂守卫，
# 于是"哪些路由可以没有凭据"这件事在**挂载处**一眼可读，而不是藏在守卫内部
# 的路径判断里。漏挂 guard 由 `tests/test_auth_boundary.py` 反向断言变红
# （无守卫的路由集合必须**恰等于** `core/auth.py::AUTH_EXEMPT_PATHS`）。
#
# ⚠️ 为什么不用"敏感读清单"：清单式是**默认放行**，漏登记即静默开放——R22 的
# 病灶（只按"写接口"分类 ⇒ 花钱/敏感 GET 整类漏保护）正是这种形态。详见 core/auth.py。
_AUTH_GUARD = [Depends(require_api_token)]

app.include_router(health_route.liveness_router, prefix="/api")  # ← 唯一豁免：存活探针
app.include_router(health_route.router, prefix="/api", dependencies=_AUTH_GUARD)  # /system/*（含花钱的 llm-probe）
app.include_router(market_route.router, prefix="/api", dependencies=_AUTH_GUARD)
app.include_router(backtest_route.router, prefix="/api", dependencies=_AUTH_GUARD)
app.include_router(watchlist_route.router, prefix="/api", dependencies=_AUTH_GUARD)
app.include_router(paper_route.router, prefix="/api", dependencies=_AUTH_GUARD)
app.include_router(review_route.router, prefix="/api", dependencies=_AUTH_GUARD)
# predict 路由已删（2026-09-08 审查 P0-4：REST 5 端点全孤立）；predict 包
# 瘦成 auto-verify 库保留（review/service.maybe_auto_verify 消费预判引擎）。
app.include_router(alert_route.router, prefix="/api", dependencies=_AUTH_GUARD)
app.include_router(risk_route.router, prefix="/api", dependencies=_AUTH_GUARD)
app.include_router(news_route.router, prefix="/api", dependencies=_AUTH_GUARD)
app.include_router(theme_catalog_route.router, prefix="/api", dependencies=_AUTH_GUARD)
app.include_router(events_route.router, prefix="/api", dependencies=_AUTH_GUARD)
app.include_router(real_position_route.router, prefix="/api", dependencies=_AUTH_GUARD)
app.include_router(agent_route.router, prefix="/api", dependencies=_AUTH_GUARD)  # AI 控制台（任务中心/审计）
app.include_router(picks_route.router, prefix="/api", dependencies=_AUTH_GUARD)
app.include_router(picks_intraday_route.router, prefix="/api", dependencies=_AUTH_GUARD)
app.include_router(assistant_route.router, prefix="/api", dependencies=_AUTH_GUARD)
app.include_router(ext_data_route.router, prefix="/api", dependencies=_AUTH_GUARD)
app.include_router(notifications_route.router, prefix="/api", dependencies=_AUTH_GUARD)
# WebSocket 走**子协议**凭据（浏览器不允许自定义请求头），无法复用上面的
# HTTP 依赖注入 ⇒ 在 `websocket/routes.py` 内于 `accept()` 之前自行校验。
app.include_router(ws_router)
