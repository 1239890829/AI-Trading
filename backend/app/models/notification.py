"""站内通知已读状态持久化（2026-09-12 缺陷修复）。

## 为什么需要一张服务端表

修复前，已读状态**只存在浏览器 localStorage**（`ashare.notifications.read`）。
localStorage 是**按 origin 命名空间**的：`http://localhost:3000` 与
`http://127.0.0.1:3000` 是两份互不可见的存储；换端口、换浏览器 profile、
内嵌预览容器（Electron 分区）、隐私模式、清站点数据 —— 任意一种都会让已读状态
**整体归零**。实测复现：同一浏览器里 localhost 侧点过「全部已读」，切到 127.0.0.1
侧徽标立刻回到 **65**（== 通知总数），与用户报告的「重启后全部变未读、显示 65 条」一致。

结论：已读状态是**用户的长期状态**，不能寄存在一个会被重新命名空间的介质里。
本表是权威存储；localStorage 降级为「首帧快速本地缓存」，两侧按**单调合并**
（水位取大、逐条 id 取并集）同步，见 `app/services/notification_read_state.py`。

## 为什么不复用 agent_param（KV 表）

`agent_param` 是**策略参数运行时覆盖层**，语义与生命周期都不同：
`agent_params.py` 会 `select(AgentParam)` **全表读取**并参与「变更存活率 / 当前覆盖值」
计算，把一条 UI 偏好塞进去会让它出现在参数控制台的未知键里，并被卷进存活率统计。
读状态与参数覆盖是两件事，各自一表。

## 字段口径

- 时间一律 **epoch 毫秒**（`BigInteger`，不用 `Integer`：epoch ms 已超 int32）；
  不用字符串时间戳——前端曾因「两种字符串格式 + 两个时区」做字面比较而整天误判已读。
- `read_ids` 存 JSON 文本（与项目其余 JSON 字段一致，不引新依赖）。
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.bjtime import beijing_now_naive
from app.models.watchlist import Base

#: 单行表：已读状态是「本机这个用户」的一份状态，没有多用户维度。
SINGLETON_ID = 1


class NotificationReadState(Base):
    __tablename__ = "notification_read_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=SINGLETON_ID)
    #: 已读水位（epoch ms）：ts ≤ 水位的条目视为已读
    seen_before: Mapped[int] = mapped_column(BigInteger, default=0)
    #: 水位之后被单独点开的条目 id（JSON 数组文本）
    read_ids: Mapped[str] = mapped_column(Text, default="[]")
    #: 一键清除水位（epoch ms）：早于它的条目整体隐藏；0 = 未清除过
    clear_before: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=beijing_now_naive, onupdate=beijing_now_naive)
