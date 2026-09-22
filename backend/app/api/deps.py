from __future__ import annotations

import hmac

from fastapi import Header, HTTPException, Request

from app.core.auth import TOKEN_HEADER, check_api_token
from app.core.config import settings
from app.repositories.watchlist_repo import WatchlistRepository
from app.services.quote_hub import QuoteHub


def normalize_symbol(symbol: str) -> str:
    """代码归一：去掉 .SH/.SZ 后缀，校验 6 位数字，非法即 400。

    S2-4：原为 `api/routes/theme_catalog.py` 的私有函数，却被
    `api/routes/events.py` 跨模块 import（`from app.api.routes.theme_catalog
    import _normalize_symbol`）。放在 `deps.py` 是刻意的——它抛 `HTTPException`，
    属 **HTTP 层**语义，不该下沉到 service（service 抛 HTTP 异常 = 层污染）。
    """
    sym = (symbol or "").strip().replace(".SH", "").replace(".SZ", "")
    if not sym.isdigit() or len(sym) != 6:
        raise HTTPException(status_code=400, detail=f"非法代码：{symbol!r}")
    return sym


def get_hub(request: Request) -> QuoteHub:
    return request.app.state.hub


def get_watchlist_repository(request: Request) -> WatchlistRepository:
    return request.app.state.watchlist_repo


async def require_api_token(request: Request) -> None:
    """**默认拒绝**的入站凭据守卫（R22 统一鉴权边界）：挂在每个 router 上（见 `main.py`）。

    与 `require_write_token` **共用同一判定**（`check_api_token`）——两个名字不是两套
    规则，而是两个**作用域声明**：本函数是"整个 API 的地板"（默认拒绝，漏挂即 401），
    `require_write_token` 是"这条路由会改状态"的显式标记（保留它是因为既有的
    `test_write_token.py::test_every_non_get_route_declares_write_guard` 靠它做机械门禁）。
    判定只有一份 ⇒ 两者**不可能漂移**。

    ⚠️ **为什么是默认拒绝而不是"敏感读清单"**：见 `core/auth.py` 的模块 docstring。
    一句话——漏登记在清单式下是**静默开放**，在默认拒绝下是**401**。

    豁免：`core/auth.py::AUTH_EXEMPT_PATHS`（全库仅 `/api/health`，且由测试钉住）。
    豁免在 `main.py` 的挂载处结构性地表达（health router 不挂本守卫），
    本函数**不做路径判断**——路径型豁免写在守卫内部就等于把"名单"藏进了逻辑里。
    """
    check_api_token(request.headers.get(TOKEN_HEADER))


async def require_write_token(request: Request) -> None:
    """写接口鉴权（技术评审 B6，opt-in 设计；R22 后与 `require_api_token` 同判定）：

    - 未配置 ``ASHARE_API_TOKEN``（本地开发默认）→ 全部放行，零影响；
    - 已配置 → 所有写请求必须携带 ``X-API-Token`` 头，
      面署到公网/NAS 时只需在 .env 配一个值即可获得写保护。

    ⚠️ **查询参数通道（``?token=``）已刻意关闭（2026-09-14）**：token 出现在 URL 上
    会被浏览器历史、`Referer` 头、反代/网关访问日志逐层留存，等于把凭据从
    「进程内存」扩散到「多份日志」。本系统前端的部署形态是同源反向代理，
    服务端注入请求头才是正路，查询参数只是历史包袱。

    ⚠️ **R22（2026-09-15）**：本守卫曾是**唯一**的入站鉴权，而它按"写接口"分类 ⇒
    花钱/敏感的 **GET**（`/assistant/daily-summary`、`/news/digest/{symbol}`、
    `/system/llm-probe?force=1`、`/real/positions` …）整类漏掉。现在这些由
    `require_api_token` 在 router 级统一盖住；本守卫保留为写作用域的显式标记。
    """
    check_api_token(request.headers.get(TOKEN_HEADER))


PROMOTION_TOKEN_HEADER = "X-Agent-Promotion-Token"


async def require_promotion_approval_token(
    promotion_token: str | None = Header(default=None, alias=PROMOTION_TOKEN_HEADER),
) -> None:
    """Independent parameter-promotion approval credential (IMP-052).

    Unlike ``require_write_token``, absence never means local-development allow.  Approval is
    a higher-authority action than ordinary writes, so the dedicated token must be explicitly
    configured, must differ from ``ASHARE_API_TOKEN``, and must match exactly.  This prevents
    model/evaluator code or an ordinary API writer from manufacturing its own promotion approval.
    """
    expected = (settings.agent_promotion_token or "").strip()
    ordinary = (settings.api_token or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="参数晋级批准入口未启用：ASHARE_AGENT_PROMOTION_TOKEN 未配置")
    if len(expected) < 32:
        raise HTTPException(status_code=503, detail="参数晋级批准凭据至少需要 32 字符")
    if ordinary and hmac.compare_digest(expected, ordinary):
        raise HTTPException(status_code=503, detail="参数晋级批准凭据不得与普通 API token 共用")
    provided = promotion_token or ""
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=403, detail="参数晋级批准凭据无效")
