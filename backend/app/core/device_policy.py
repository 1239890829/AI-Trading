"""Reviewed browser capabilities. New endpoints require maintenance by default.

These are actual product consumers, not a blanket GET exemption. Authentication
is always checked first. Service credentials remain outside the browser lane.
"""
from collections.abc import Mapping
from typing import Any


def matched_route_template(scope: Mapping[str, Any]) -> str:
    """Resolve the matched template, including FastAPI include/mount prefixes.

    FastAPI 0.137+ preserves the original APIRoute in scope['route']; its path
    omits include_router prefixes. The effective context owns those prefixes.
    Never infer authorization from the request URL or a partial route identity.
    """
    route = scope.get("route")
    if route is None:
        return ""
    fastapi_scope = scope.get("fastapi", {})
    context = fastapi_scope.get("effective_route_context") if isinstance(fastapi_scope, Mapping) else None
    if context is not None:
        if getattr(context, "original_route", None) is not route:
            return ""
        path = getattr(context, "path", None)
    else:
        path = getattr(route, "path", None)
    root = scope.get("root_path", "")
    if not isinstance(path, str) or not path.startswith("/") or not isinstance(root, str):
        return ""
    if root and not root.startswith("/"):
        return ""
    return root.rstrip("/") + path


READ_PATHS = frozenset({
    "/picks/watcher/state", "/picks/signal-health",
    "/picks/strategy-health", "/picks/strategy-registry",
    "/agent/kb/tree", "/agent/kb/file",
    '/announcements/{symbol}',
    '/assistant/daily-summary',
    '/assistant/entity-dict',
    '/auction-benchmark',
    '/auction-premium',
    '/auction/{symbol}',
    '/boards',
    '/capital-flow/{symbol}',
    '/chip',
    '/company/{symbol}',
    '/events',
    '/events/flash-coverage',
    '/events/focus/themes',
    '/events/impact',
    '/events/symbol/{symbol}',
    '/events/symbol/{symbol}/attribution',
    '/events/verify',
    '/events/{event_id}',
    '/events/{event_id}/attribution',
    '/events/{event_id}/interpretation',
    '/events/{event_id}/stocks',
    '/financials/{symbol}',
    '/indices/{symbol}/members',
    '/kline/{symbol}',
    '/limit-down',
    '/limit-up',
    '/longhu',
    '/longhu/{symbol}',
    '/market/anomalies',
    '/market/anomalies/stock',
    '/market/board-fund-flow',
    '/market/board-fund-flow/{board_code}/members',
    '/market/board-fund-flow/{board_code}/minute',
    '/market/board-fund/by-symbols',
    '/market/breadth',
    '/market/entry-checklist',
    '/market/fund-flow',
    '/market/fund-flow/history',
    '/market/fund-flow/intraday',
    '/market/heat/rank-trend',
    '/market/heat/skyrocket',
    '/market/heatmap',
    '/market/ladder-check',
    '/market/longhu/theme-trail',
    '/market/overview',
    '/market/sentiment',
    '/market/sentiment-history',
    '/market/turnover',
    '/market/turnover/day',
    '/market/turnover/history',
    '/minute-line/{symbol}',
    '/news/content',
    '/news/digest/{symbol}',
    '/news/{symbol}',
    '/notifications',
    '/notifications/diagnostics',
    '/notifications/read-state',
    '/order-book/{symbol}',
    '/picks/board-surge',
    '/picks/execution-gate',
    '/picks/history',
    '/picks/intraday-opportunities',
    '/picks/intraday-review',
    '/picks/intraday-top',
    '/picks/leader-archive',
    '/picks/leader-research',
    '/picks/lurk-pool',
    '/picks/meta',
    '/picks/morning-brief/today',
    '/picks/opportunities',
    '/picks/opportunity-scorecard',
    '/picks/position-labels',
    '/picks/relay-rank',
    '/picks/review',
    '/picks/today',
    '/picks/validation',
    '/picks/validation/entries',
    '/picks/watch-ledger',
    '/quotes',
    '/quotes/{symbol}',
    '/real/positions',
    '/review/action-items',
    '/review/effectiveness',
    '/review/reports',
    '/review/reports/{trade_date}',
    '/risk/state',
    '/search',
    '/sparkline',
    '/speed-rank',
    '/stock-flow',
    '/themes',
    '/themes/catalog',
    '/themes/catalog/index',
    '/themes/catalog/strength',
    '/themes/catalog/{code}/detail',
    '/themes/catalog/{code}/members',
    '/themes/hot',
    '/themes/stock/{symbol}',
    '/trades/{symbol}',
    '/watchlist',
    '/watchlist/groups',
})
WRITE_PATHS = frozenset({
    ("POST", "/watchlist"), ("DELETE", "/watchlist/{symbol}"),
    ("POST", "/watchlist/groups"), ("PUT", "/watchlist/groups/{name}"),
    ("DELETE", "/watchlist/groups/{name}"), ("PUT", "/watchlist/{symbol}/group"),
    ("POST", "/real/trades"), ("DELETE", "/real/trades/{trade_id}"),
    ("PATCH", "/real/positions/{symbol}"), ("DELETE", "/real/positions/{symbol}"),
    ("PUT", "/notifications/read-state"), ("POST", "/assistant/chat"),
})


_READ = frozenset("/api" + path for path in READ_PATHS)
_WRITE = frozenset((method, "/api" + path) for method, path in WRITE_PATHS)


def needs_maintenance(method: str, route_path: str) -> bool:
    """Match the registered route template, never a user-controlled URL shape.

    A newly added /quotes/internal-export must not inherit /quotes/{symbol}.
    Missing route identity fails closed.
    """
    path = route_path.rstrip("/")
    if method == "GET" and path in _READ:
        return False
    return (method, path) not in _WRITE
