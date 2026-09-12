from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import utcnow
from app.models.watchlist import Base


class Theme(Base):
    """题材字典（同花顺官方概念目录，architecture-design §1）。

    此前题材名只存在于涨停股 reason 串里——无涨停发生的题材（如粮食概念）
    在系统里不存在。本表以官方目录为全集，是题材—梯队—个股映射的地基。
    """

    __tablename__ = "theme"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # 同花顺板块代码，如 885995.TI
    code: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(64))
    # 目前仅 ths_official；预留 em_concept / manual
    source: Mapped[str] = mapped_column(String(16), default="ths_official")
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class ThemeMember(Base):
    """题材归属（成分股快照）。

    归属来源分层（architecture-design §1，高层压制低层、并列不覆盖）：
    ths_official=官方成分（结构性）> limit_up_reason=涨停归因（行为性）
    > event_infer=事件推断（推断性）> 人工 override 直接裁决。
    本表只存官方成分；其余两层在展示时叠加，不落库。
    """

    __tablename__ = "theme_member"
    __table_args__ = (UniqueConstraint("theme_code", "symbol", name="uq_theme_member"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    theme_code: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(6), index=True)
    name: Mapped[str] = mapped_column(String(64), default="")
    attribution_source: Mapped[str] = mapped_column(String(16), default="ths_official")
    synced_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class ThemeOverride(Base):
    """人工归属纠错（architecture-design §1）。权重高于一切自动归属。"""

    __tablename__ = "theme_override"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    theme_code: Mapped[str] = mapped_column(String(16), index=True)
    symbol: Mapped[str] = mapped_column(String(6), index=True)
    # include = 人工加入归属；exclude = 人工排除官方成分
    action: Mapped[str] = mapped_column(String(8), default="include")
    reason: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    # 为空表示长期有效；到期的 override 由读取方过滤
    expires_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
