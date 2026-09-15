"""助手受限工具调用（P0-3）—— **包门面**。

2026-09-15（IMP-005）按业务域拆成 `app/assistant/tools/*.py`：本文件**只做转发**，
不含实现。⚠️ **Python 没有 `export`**：原文件的所有顶层名（含 `_valid_date` 这类
私有名，实测被 tests 直接 import）都必须在外部可见面上 —— 因此这里 re-export 全部名字，
并由 `__all__` 显式登记，避免「看起来没漏、其实少了几个」的静默漂移。

新增工具请加到**对应域**的模块里并登记进 `registry.py` 的 TOOL_SPECS，不要往本文件塞实现。
"""
from __future__ import annotations

from .core import (
    MAX_CALLS_PER_TURN,
    MAX_CHARS,
    MAX_ROWS,
    MAX_SYMBOLS,
    MAX_TOOL_ROUNDS,
    TIMEFRAMES,
    TOOL_CACHE,
    ToolCall,
    ToolContext,
    ToolSpec,
    _DATE_RE,
    _INDEX_RE,
    _SYM_RE,
    _TOOL_RE,
    _clip,
    _fmt_rows,
    _fmt_yi,
    _int_arg,
    _latest_trade_day,
    _one_symbol,
    _rec,
    _resolve_date,
    _valid_date,
    _valid_symbols,
    has_partial_tool_call,
    log,
    parse_tool_calls,
    strip_tool_calls,
)

from .registry import (
    TOOL_LABELS,
    TOOL_SPECS,
    tool_label,
    tool_manifest,
)

from .dispatch import (
    run_tool,
    run_tool_calls,
)

from .market import (
    _longhu_stock,
    _snapshot_map_sync,
    _t_auction,
    _t_basics,
    _t_board_flow,
    _t_boards,
    _t_capital_flow,
    _t_commodity,
    _t_kline,
    _t_limit_break,
    _t_limit_down,
    _t_limit_up,
    _t_longhu,
    _t_market_overview,
    _t_minute,
    _t_orderbook,
    _t_quotes,
    _t_trades,
)

from .themes import (
    _t_hot,
    _t_theme_members,
    _t_themes,
)

from .picks import (
    _t_anomaly,
    _t_backtest,
    _t_factor_profile,
    _t_minute_decisions,
    _t_picks,
)

from .agent import (
    _t_agent_tasks,
    _t_brief,
    _t_climate,
    _t_param_changes,
    _t_review,
)

from .events import (
    _t_alert_events,
    _t_chain,
    _t_events,
    _t_news,
    _t_sentiment,
)

from .account import (
    _t_paper,
    _t_positions,
    _t_watchlist,
)

__all__ = [
    "MAX_CALLS_PER_TURN",
    "MAX_CHARS",
    "MAX_ROWS",
    "MAX_SYMBOLS",
    "MAX_TOOL_ROUNDS",
    "TIMEFRAMES",
    "TOOL_CACHE",
    "TOOL_LABELS",
    "TOOL_SPECS",
    "ToolCall",
    "ToolContext",
    "ToolSpec",
    "_DATE_RE",
    "_INDEX_RE",
    "_SYM_RE",
    "_TOOL_RE",
    "_clip",
    "_fmt_rows",
    "_fmt_yi",
    "_int_arg",
    "_latest_trade_day",
    "_longhu_stock",
    "_one_symbol",
    "_rec",
    "_resolve_date",
    "_snapshot_map_sync",
    "_t_agent_tasks",
    "_t_alert_events",
    "_t_anomaly",
    "_t_auction",
    "_t_backtest",
    "_t_basics",
    "_t_board_flow",
    "_t_boards",
    "_t_brief",
    "_t_capital_flow",
    "_t_chain",
    "_t_climate",
    "_t_commodity",
    "_t_events",
    "_t_factor_profile",
    "_t_hot",
    "_t_kline",
    "_t_limit_break",
    "_t_limit_down",
    "_t_limit_up",
    "_t_longhu",
    "_t_market_overview",
    "_t_minute",
    "_t_minute_decisions",
    "_t_news",
    "_t_orderbook",
    "_t_paper",
    "_t_param_changes",
    "_t_picks",
    "_t_positions",
    "_t_quotes",
    "_t_review",
    "_t_sentiment",
    "_t_theme_members",
    "_t_themes",
    "_t_trades",
    "_t_watchlist",
    "_valid_date",
    "_valid_symbols",
    "has_partial_tool_call",
    "log",
    "parse_tool_calls",
    "run_tool",
    "run_tool_calls",
    "strip_tool_calls",
    "tool_label",
    "tool_manifest",
]
