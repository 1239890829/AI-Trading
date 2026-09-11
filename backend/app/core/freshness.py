"""统一的「新鲜度 / 降级」契约（S2-1，2026-09-11）。

**为什么要有这个模块**：同一个「这份数据还能不能用」的问题，此前在系统里被
表达了 8 遍，字段名与判据各不相同——`quality`（五级枚举）/ `is_stale`
（布尔，阈值写死在 QuoteHub）/ `snapshot_age_seconds`（秒数，消费方各自比大小）/
`consecutive_failures`（次数）/ `last_error`（字符串）/ `state`（exit_engine 的
三态）/ `age_seconds` / `reason`。后果有两类，都已真实发生：

- **判据缺失**：`market_context` 只判 `breadth is None`、不判年龄 ⇒
  20 分钟前的宽度配上当前涨停池照样出「阶段」结论——**数字全都合理、结论是错的**；
- **判据不可组合**：调用方要回答"能不能用它下结论"，得同时读三个字段并自行
  拼装阈值，于是每个调用方都写一套，漏一个就静默降级。

本模块把该问题收敛为一个值对象：`{state, as_of, age_seconds, reason, source}`。

**状态语义**（五态，不是二态——`unknown` 必须显式，禁止用默认值冒充判定）：

| state | 含义 | 典型来源 |
|---|---|---|
| `ready` | 有数据且在新鲜窗口内 | 正常轮询命中 |
| `stale` | 有数据但超出新鲜窗口 | 上游停更、轮询超时 |
| `degraded` | 有数据但来自降级路径（备用源/部分字段缺） | 四源链 failover、补价失败 |
| `unavailable` | 没有可用数据 | 首次拉取失败、尚未就绪 |
| `unknown` | 无法判定（连时间戳都没有） | 缺 `as_of` 且无失败计数 |

`reason` 必须是**给人看的中文**且**不含易变数值**（耗时/请求 id）——
它会被哨兵按字符串去重，含变量会把去重打穿、退化成每轮推一次飞书
（该教训见 `services/data_health_loop.py`）。
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel

#: 缺省新鲜窗口（秒）。调用方应优先传自己的轮询周期派生值，不要一律用这个。
DEFAULT_FRESH_WITHIN_SECONDS = 60.0


def age_seconds_of(as_of: datetime | None) -> float | None:
    """`as_of` 距现在的秒数。naive 时间戳**按 UTC 解释**（本仓库存的一律是 UTC）。

    与 `datetime.now(utc)` 之差为负（时钟回拨/上游给未来时间）时返回 `0.0`
    而不是负数——负年龄会让所有 `age > 阈值` 的比较恒假、静默变成"永远新鲜"。
    """
    if as_of is None:
        return None
    ref = as_of if as_of.tzinfo is not None else as_of.replace(tzinfo=timezone.utc)
    return max(0.0, round((datetime.now(timezone.utc) - ref).total_seconds(), 1))


class Freshness(BaseModel):
    """一份数据源/字段的「可用性 + 新鲜度」判定。"""

    state: str = "unknown"
    as_of: datetime | None = None
    age_seconds: float | None = None
    reason: str | None = None
    source: str | None = None

    # ---------- 构造 ----------

    @classmethod
    def ready(cls, *, as_of: datetime | None = None, source: str | None = None,
              age_seconds: float | None = None) -> "Freshness":
        return cls(state="ready", as_of=as_of,
                   age_seconds=age_seconds if age_seconds is not None else age_seconds_of(as_of),
                   source=source)

    @classmethod
    def stale(cls, *, reason: str, as_of: datetime | None = None,
              age_seconds: float | None = None, source: str | None = None) -> "Freshness":
        return cls(state="stale", as_of=as_of,
                   age_seconds=age_seconds if age_seconds is not None else age_seconds_of(as_of),
                   reason=reason, source=source)

    @classmethod
    def degraded(cls, *, reason: str, as_of: datetime | None = None,
                 age_seconds: float | None = None, source: str | None = None) -> "Freshness":
        return cls(state="degraded", as_of=as_of,
                   age_seconds=age_seconds if age_seconds is not None else age_seconds_of(as_of),
                   reason=reason, source=source)

    @classmethod
    def unavailable(cls, *, reason: str, as_of: datetime | None = None,
                    source: str | None = None) -> "Freshness":
        return cls(state="unavailable", as_of=as_of,
                   age_seconds=age_seconds_of(as_of), reason=reason, source=source)

    @classmethod
    def unknown(cls, *, reason: str, source: str | None = None) -> "Freshness":
        return cls(state="unknown", reason=reason, source=source)

    @classmethod
    def from_age(cls, *, as_of: datetime | None, fresh_within: float,
                 source: str | None = None,
                 missing_reason: str = "无时间戳，无法判定新鲜度") -> "Freshness":
        """按「年龄 vs 新鲜窗口」派生——绝大多数调用点只需要这个。

        `as_of` 为 None ⇒ `unknown`（**不是** ready）：没有时间戳就无从判新鲜，
        按 `ready` 处理会让"从未成功过"被当成"刚刚成功过"。
        """
        age = age_seconds_of(as_of)
        if age is None:
            return cls.unknown(reason=missing_reason, source=source)
        if age > fresh_within:
            return cls.stale(
                as_of=as_of, age_seconds=age, source=source,
                reason=f"数据滞后 {age:.0f} 秒，超出新鲜窗口 {fresh_within:.0f} 秒",
            )
        return cls.ready(as_of=as_of, age_seconds=age, source=source)

    # ---------- 判定 ----------

    def is_usable(self) -> bool:
        """有数据可用（`ready` / `degraded` / `stale`）。

        ⚠️ 注意 `stale` 也返回 True——**"有数据"与"敢不敢据此下结论"是两件事**，
        后者必须显式判（见 `is_fresh`）。这是本契约刻意不合并的一个区别：
        历史事故正是"有数据就当新鲜用"。
        """
        return self.state in ("ready", "degraded", "stale")

    def is_fresh(self) -> bool:
        """数据新鲜到可以直接支撑结论（只有 `ready`）。"""
        return self.state == "ready"

    def note(self, label: str) -> str:
        """给「结论降级说明」用的中文短句（含标签，供 caveats / reason 复用）。

        刻意不在这里再拼一次年龄——`stale` 的 `reason` 已含滞后秒数，
        重复拼接会得到「数据滞后 300 秒…（已滞后 300 秒）」这类冗余文案。
        """
        if self.state == "ready":
            return f"{label}正常"
        if self.state == "unknown":
            return f"{label}无法判定新鲜度（{self.reason or '无时间戳'}）"
        if self.state == "unavailable":
            return f"{label}不可用（{self.reason or '未知原因'}）"
        return f"{label}：{self.reason or self.state}"
