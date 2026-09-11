"""北京时间唯一权威（S2-8，2026-09-11）。

## 为什么单独建一个模块

收敛前全站有 **20 处** 重复的时区常量定义（`_TZ_BJ` ×5 / `_BJ` ×4 / `_BJT` ×3 /
`BJ_OFFSET` ×2 / `_TZ_SH` / `BJ` / `sh` / `_BJ_DELTA` / `CST` / `cst`）、**8 个**
各自实现的「当前北京时间」函数，以及 4 处 `datetime.now(timezone.utc) +
timedelta(hours=8)` 裸算术。同一件事有三十多种写法 ⇒ **改一处口径不可能改全**，
而漏掉的那一处不会被任何测试发现。

**为什么放在 `core/`**：分层守卫（`tests/test_import_lint.py`）规定业务层不得
反向依赖，`core/` 是最底层。`factors/`、`review/`、`api/` 要取当前时刻，不该为此
import 整个行情日历模块（`market/trading_status.py`）。本模块**零 app 依赖**
（只 import `datetime`），任何层引用都不会成环——`events/extract.py` 里那处
「独立实现防循环导入」的借口随之消失。

## 两种语义，不要混用

| 函数 | 返回 | 用途 |
|---|---|---|
| `beijing_now()` | **aware**（tzinfo=UTC+8） | 计算 / 比较 / `.date()` / `.strftime()` |
| `beijing_now_naive()` | **naive** | 落库与展示的**事件时间口径** |

事件时间统一存北京 naive（2026-09-09 定为口径，事故背景见 `beijing_now_naive` 的 docstring）。
aware 与 naive **相减会抛 TypeError**——这正是当年「早了 8 小时」事故的类型级防线，
**不要**为了「方便」把两者合并成一个返回值。

## 为什么是固定偏移而不是 ZoneInfo("Asia/Shanghai")

中国全境不实行夏令时，**当前时刻**恒为 UTC+8，固定偏移语义上完全正确；
而 `ZoneInfo` 依赖运行环境带 tzdata，缺失时会在 import 期抛异常——用一次
不会失败的实现换取一个会失败的实现不划算。若将来需要处理 1986–1991 年的
历史夏令时（本项目只取当前时刻，用不到），再换 ZoneInfo 并补 tzdata 依赖。

时区名刻意**不传**（`timezone(timedelta(hours=8))` 而非 `..., "CST"`）：
这样 `repr` 与 `isoformat()` 输出与收敛前 20 处定义**逐字节一致**，
是「零语义变更」的机械收敛，不是口径调整。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

#: 北京时区（UTC+8）。**全站唯一时区常量**。
#: 新代码一律 `from app.core.bjtime import BJ_TZ`，不得再写
#: `timezone(timedelta(hours=8))`——守卫见 `tests/test_bjtime.py`。
BJ_TZ = timezone(timedelta(hours=8))

#: 北京相对 UTC 的固定偏移，供对 **naive** 时间戳做算术的场景使用
#: （如 `datetime.fromisoformat(ts) + BJ_OFFSET`）。与 `BJ_TZ` 同源，勿单独改。
BJ_OFFSET = timedelta(hours=8)


def beijing_now() -> datetime:
    """当前北京时间（**aware**）。"""
    return datetime.now(BJ_TZ)


def beijing_now_naive() -> datetime:
    """当前北京时间（**naive**）——事件时间的存储/展示口径。

    **为什么是 naive 而不是 aware**：事件时间直接落 SQLite 的 naive 列，
    再原样序列化给前端；带 tzinfo 的 aware 值经 FastAPI 序列化会带上
    `+08:00` 偏移，而多数消费方（`lib/format.ts::eventTimeText` 一类）
    按纯字符串截取展示，偏移反而变成噪音。口径统一为「北京 naive」。

    **事故背景（2026-09-09 定为口径）**：`event_card.published_at` 曾存 UTC
    （flash 入库时 `astimezone(utc)`），而展示/排序按北京时间理解 →
    全部时间「早了 8 小时」（用户看到的事件全是早上）。事件面向人看，
    统一存北京 naive；ranking/judge 等内部计算同步对齐此口径。

    ⚠️ 与之相对，**API 出口若发 naive 串，前端不得再当 UTC 解析**——
    没有时区标记的 ISO 串，`new Date()` 只能按**运行环境本地时区**理解。
    这正是 2026-09-11 「交易智能体时间早 8 小时」的根因（agent 域当时仍是
    `utcnow` naive，前端 `timeText` 按本地解析 → 原样显示 UTC 墙钟）。
    """
    return datetime.now(BJ_TZ).replace(tzinfo=None)


def beijing_today() -> date:
    """北京时区的今天。

    **不要用 `date.today()`**——容器/本机时区不一定是 CST，跨零点会差一天。
    """
    return datetime.now(BJ_TZ).date()


def to_beijing(ts: datetime) -> datetime:
    """任意 datetime → 北京 **aware**。

    naive 输入**视为已经是北京时间**（不按 UTC 解释）——这是 2026-09-09 定的口径，
    与 `events/extract.py`、`events/store.py` 的历史处理保持一致。
    """
    if ts.tzinfo is None:
        return ts.replace(tzinfo=BJ_TZ)
    return ts.astimezone(BJ_TZ)


def to_beijing_naive(ts: datetime) -> datetime:
    """任意 datetime → 北京 **naive**（事件时间口径的统一转换口）。"""
    return to_beijing(ts).replace(tzinfo=None)


__all__ = [
    "BJ_OFFSET",
    "BJ_TZ",
    "beijing_now",
    "beijing_now_naive",
    "beijing_today",
    "to_beijing",
    "to_beijing_naive",
]
