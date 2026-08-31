"""事件驱动选股模块（linkage-design §4）：事件抽取、存储与标的池映射。"""

from app.events.store import EventStore

__all__ = ["EventStore"]
