"""市场路由**门面**（IMP-005 批 3 切片后的唯一装配点，2026-09-15）。

本文件原先有 **1799 行 / 50 个端点**，已按业务域切分为 8 个分片（同目录
`market_*.py`），并保留本文件作为**唯一装配点**—— `app/main.py` 与测试
只认 `market.router`，切片对它们**零改动**。

**为什么保留门面而不是让 `main.py` 直接装配 8 份**：切片前的对外契约是
「`market.router` 一个对象」；让 `main.py` 逐个装配会把「市场路由有哪几片」
这一实现细节泄漏到装配层，且路径/标签的拼装规则分散到 8 处。

**分片顺序**按「该域在原文件中的最早端点行号」排列 ⇒ 注册顺序尽量贴近原顺序。
⚠️ **无法逐条相同**：原文件里各域端点**交错**（如题材域的总览在 538 行、
筹码在 1772 行），重排后同一域会聚到一起。故判据不写成「顺序不变」，而是
**对外契约（OpenAPI）逐字不变 + 无路径遮蔽**（见 `docs/kb/09-verification-pitfalls.md`）。

**装配走 `include_router`**：tags 由**子 router 各自声明**（均为 `["market"]`，与原文件一致）。
⚠️ 门面**不得**再声明 `tags=["market"]` —— `include_router` 对 tags 做的是**合并**，
两边都写会得到 `["market", "market"]`。本项由契约判据实测抓到：paths/ops/schemas
计数与参数全对，**只有 tags 不一致** —— 若判据只比"路由表条数"就会漏掉。
"""

from fastapi import APIRouter

from app.api.routes import market_sentiment as _market_sentiment_mod
from app.api.routes import market_quotes as _market_quotes_mod
from app.api.routes import market_flow as _market_flow_mod
from app.api.routes import market_themes as _market_themes_mod
from app.api.routes import market_longhu as _market_longhu_mod
from app.api.routes import market_pools as _market_pools_mod
from app.api.routes import market_board as _market_board_mod
from app.api.routes import market_stock as _market_stock_mod

#: 市场路由（对外契约仍是「一个 router」，切片对 main.py 与测试零改动）
#: ⚠️ 门面**刻意不声明 tags**：include_router 会把它与子 router 的 tags **合并**，
#: 两边都写 ["market"] 会得到 ["market", "market"]（契约判据实测抓到）。
router = APIRouter()

router.include_router(_market_sentiment_mod.router)
router.include_router(_market_quotes_mod.router)
router.include_router(_market_flow_mod.router)
router.include_router(_market_themes_mod.router)
router.include_router(_market_longhu_mod.router)
router.include_router(_market_pools_mod.router)
router.include_router(_market_board_mod.router)
router.include_router(_market_stock_mod.router)
