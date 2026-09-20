"""市场路由的**信封元数据**构建（从 `market.py` 抽出，2026-09-15 IMP-005 第三批）。

**为什么单独一个模块**：这 4 个辅助被 `market.py` 的**几乎所有端点**共用
（`meta_payload` 实测被 **39 个**函数引用），按业务域切分时它们必然跨分片；
而 `tests/test_import_lint.py::test_routes_do_not_import_each_others_privates`
**禁止路由之间 import 私有名**（下划线开头）⇒ 跨分片共享的辅助必须是**公开名**。
本模块因此只放「与业务无关、纯信封拼装」的东西：新鲜度、meta、按日期的降级 meta。

**不属于这里的东西**（刻意留在各域分片内）：只被单一域使用的私有辅助
（如 `_valid_board_code` / `_kline_payload` / `_speed_sampler`）——它们随域搬走，
搬过来只会把「域内实现细节」提升成跨域公共 API。

⚠️ 名字去掉下划线**不是**为了好看：私有名跨模块引用正是这段历史被守卫拦下的原因
（见 `test_routes_do_not_import_each_others_privates` 的 docstring）。
"""

from __future__ import annotations
import logging
from datetime import date
from app.core.freshness import Freshness
from app.market.trade_calendar import trading_days
from app.schemas.market import utcnow
from app.services.quote_hub import QuoteHub
from app.services.market_snapshot import default_trade_date_weekend_fallback
from app.core.bjtime import beijing_today

log = logging.getLogger("app.api.routes.market")  # 沿用原 logger 名（日志来源不变）


def hub_freshness(hub: QuoteHub) -> dict:
    """行情链新鲜度（S2-1 契约）。**保留 `is_stale` 布尔**供既有消费方使用。

    刻意用 `getattr` 回退而非直接调 `hub.freshness()`：多处测试桩只实现了
    `is_stale`（历史接口面），强依赖新方法会把"加一个只读字段"变成破坏性改动。
    回退路径同样按 Freshness 规则派生（缺时间戳 → unknown），**不假装 ready**。
    """
    fn = getattr(hub, "freshness", None)
    if callable(fn):
        fresh = fn()
    else:
        fresh = Freshness.from_age(
            as_of=getattr(hub, "last_success_refresh", None),
            fresh_within=getattr(hub, "stale_after", 10.0) or 10.0,
            source=getattr(getattr(hub, "provider", None), "name", None),
            missing_reason="行情链未提供成功刷新时间，无法判定新鲜度",
        )
    return fresh.model_dump(mode="json")


def meta_payload(hub: QuoteHub) -> dict:
    """行情信封的 meta。

    `batch_coverage`（R18，2026-09-14）：上一轮批量请求的**返回覆盖率**
    （1.0 = 请求集全部返回，0.0 = 一只都没回，None = 未判定/空自选）。
    它量的是"源有没有漏返回"，与 `freshness`（量时间年龄）是两个不同的轴：
    源可能每一轮都"成功"却稳定漏掉几只，此时 Hub 级 freshness 仍 ready——
    逐标的 `Quote.quality`/`freshness()` 负责诚实，本字段提供**系统级**读数
    （例如"覆盖率从 1.0 掉到 0.92 并持续"是源侧退化的早期信号）。

    ⚠️ 刻意**不**把完整缺失清单放进每个响应：自选可达数百只，全量列表是
    纯载荷浪费。清单保留在 Hub 对象上（`hub.last_missing_symbols`）供日志
    与诊断使用，日志侧只在缺失集**变化**时打印。

    `index_batch` 则核对固定的 6 个基准指数，包含首次缺席的代码与覆盖率。
    它描述最近完成的批次，覆盖率 None 表示尚未判定；请求失败仍保留旧批次证据，
    须结合 last_success_refresh/freshness 判断年龄，不能视为最新请求成功证明。

    `push`（R24，2026-09-14）：WebSocket 推送链路的取证面 —— 订阅连接数、
    出站队列上限、**累计丢弃帧数**。丢弃只可能发生在出站队列满时，即客户端
    停止消费而服务端仍在按 1Hz 生产（慢网络 / 半死连接 / 已成孤儿的 writer）；
    它是"推送正在丢帧"的唯一系统级读数，也是判定"该降级了"的依据之一。
    """
    return {
        "provider": hub.provider.name,
        "is_realtime": bool(getattr(hub.provider, "realtime", False)) and not hub.is_stale(),
        "is_stale": hub.is_stale(),
        "freshness": hub_freshness(hub),
        "batch_coverage": getattr(hub, "last_batch_coverage", None),
        "index_batch": hub.index_batch() if hasattr(hub, "index_batch") else None,
        "source_rejections": hub.source_rejections() if hasattr(hub, "source_rejections") else None,
        "push": hub.subscriber_stats() if hasattr(hub, "subscriber_stats") else None,
        "last_success_refresh": hub.last_success_refresh.isoformat() if hub.last_success_refresh else None,
        "generated_at": utcnow().isoformat(),
    }


async def dated_meta(hub: QuoteHub, trade_date: date) -> dict:
    """**按日期取数**的载荷专用 meta：数据日期落后于最近交易日时**必须降级**（红线 2）。

    为什么需要它（2026-09-14 实测）：`meta_payload()` 描述的是 **Hub 的实时健康度**，而 `data`
    的业务日期是**另一个轴**；此前二者之间**没有任何一致性校验**，于是同一响应里并存
    `data.trade_date = "2026-09-11"`、`meta.is_realtime = true`、`meta.is_stale = false`、
    `freshness.state = "ready"`、`freshness.age_seconds = 0.9` —— **过期数据拿到了最强的
    实时背书**。这正是红线 2「禁止把过期缓存冒充实盘」要禁止的形态。

    判据用**日历覆盖的最近交易日**（`latest_trade_date` 已走日历唯一入口），
    不引入第二套日期口径。日期不落后时原样返回，不做任何额外断言。
    """
    meta = meta_payload(hub)
    latest = await latest_trade_date(hub)
    if trade_date >= latest:
        return meta
    meta["is_realtime"] = False
    meta["is_stale"] = True
    fresh = dict(meta.get("freshness") or {})
    fresh["state"] = "stale"
    fresh["reason"] = f"数据日期 {trade_date.isoformat()} 早于最近交易日 {latest.isoformat()}"
    meta["freshness"] = fresh
    # 显式给出「实际日期 / 应有日期」，让前端与调试都不必猜（三态：不隐藏落后）
    meta["data_date"] = trade_date.isoformat()
    meta["expected_date"] = latest.isoformat()
    return meta


async def latest_trade_date(hub: QuoteHub) -> date:
    """最近一个**交易日**（走日历唯一入口 `trading_days`）。

    原实现直接调 `default_trade_date_weekend_fallback()`——它只处理周六/周日，
    遇节假日（如国庆假期里的工作日）会返回**当天这个非交易日**，龙虎榜必然返回空；
    用户看到"没有数据"却无法区分"确实没上榜"与"查的是非交易日"。
    日历不可用才退到周末规则（并记 warning，不静默）。
    """
    try:
        days = await trading_days(hub.provider)
        past = [d for d in days if d <= beijing_today()]
        if past:
            return max(past)
        log.warning("longhu: 交易日历无 ≤ 今日的交易日，退到周末回退规则")
    except Exception:  # noqa: BLE001  日历不可用不该让龙虎榜 500
        log.warning("longhu: 交易日历不可用，退到周末回退规则", exc_info=True)
    return default_trade_date_weekend_fallback()
