"""Service credential policy and legacy non-browser WS encoding.

HTTP service callers still use X-API-Token. Browser requests also require a
paired session and an explicit capability check in api/deps.py. The only HTTP
exemption is /api/health. Browser WS connections use one-use device tickets;
long-term service tokens are never sent to the browser. Base64 is encoding,
not encryption or an identity boundary. See docs/system/deployment.md.
"""

from __future__ import annotations

import base64
import binascii
import hmac

from fastapi import HTTPException

from app.core.config import settings

#: `local`：假定只在回环上服务，token 留空即全放行（本地开发零摩擦）。
AUTH_MODE_LOCAL = "local"
#: `shared`：假定会有回环之外的访客，token **必配**，未配即拒绝启动。
AUTH_MODE_SHARED = "shared"

AUTH_MODES: tuple[str, ...] = (AUTH_MODE_LOCAL, AUTH_MODE_SHARED)

#: HTTP 面的凭据头。刻意只留这一条通道：查询参数通道已被关闭（见模块 docstring）。
TOKEN_HEADER = "X-API-Token"

#: WebSocket 子协议前缀。后接 `base64url(token)`（去填充）。
WS_SUBPROTOCOL_PREFIX = "ashare-token."

#: 唯一允许**不带凭据**的路由（全库仅此一条）。
#:
#: 为什么需要这份登记：守卫是**默认拒绝**的（`main.py` 在每个 `include_router`
#: 上挂 `require_api_token`）。默认拒绝的可靠性完全取决于「例外是否被显式登记」——
#: 没有这份清单时，某个 router 漏挂守卫就是**静默开放**；有了它，守卫可以反向断言
#: 「无守卫的路由集合 == 本清单」，把"漏挂"与"悄悄新增豁免"**一起变成变红**。
#:
#: 为何 `/api/health` 可以豁免：它是存活探针，被 `docker-compose.prod.yml` 的
#: healthcheck 以**不带任何凭据**的方式调用（`urllib.request.urlopen`），若要求
#: 凭据则容器永远不健康。它只回运行状态与版本，**不读持仓、不触发 LLM**，
#: 故 R22 的验收（"无凭据时在读持仓/启动LLM之前 401"）对它不适用。
AUTH_EXEMPT_PATHS: frozenset[str] = frozenset({"/api/health"})

_UNAUTHORIZED_DETAIL = "该操作需要 X-API-Token（或 WebSocket 子协议凭据）：服务端已启用访问鉴权"


def validate_auth_posture() -> None:
    """启动期校验（fail closed）：配置自相矛盾时**拒绝启动**，而不是运行期静默放行。

    判据只有两条，都是有明确"正确值"的形态判定，不做任何网络探测：

    1. `auth_mode` 必须是已知取值——写错一个字母（`shared` → `share`）会让
       `auth_required` 静默退回 `local`，即**把共享部署降级成全开**。这是典型的
       "配置拼错 = 静默降级"，必须拒绝而不是忽略。
    2. `shared` 姿态必须配 token——这正是本姿态存在的意义（见 `core/config.py` 注释）。

    在 `app/main.py` 模块级调用（即导入应用对象时立刻生效），使**任何**启动路径
    （uvicorn / 测试 / 脚本）都拿到同一个结论。
    """
    mode = (settings.auth_mode or "").strip().lower()
    if mode not in AUTH_MODES:
        raise RuntimeError(
            f"ASHARE_AUTH_MODE={settings.auth_mode!r} 取值非法，只接受 {list(AUTH_MODES)}。"
            "（拼错会被静默当作 local，即'共享部署降级为全放行'，故宁可拒绝启动）"
        )
    if mode == AUTH_MODE_SHARED and not settings.api_token:
        raise RuntimeError(
            "ASHARE_AUTH_MODE=shared 但 ASHARE_API_TOKEN 为空 ⇒ 拒绝启动（fail closed）。"
            "shared 姿态会接受回环之外的访客，无凭据启动等于把写接口、敏感读与 LLM 调用"
            "暴露给任意能连上该端口的人。请配置一个随机串，例如 `openssl rand -hex 32`；"
            "若确实只在本机使用，请改用 ASHARE_AUTH_MODE=local（默认值）。"
        )


def is_api_token_valid(provided: str | None) -> bool:
    """凭据是否被接受——**纯判定，不抛异常**（判定本体，`check_api_token` 只是它的 HTTP 包装）。

    HTTP 面用 `check_api_token`（要 401），WebSocket 面用本函数（握手阶段没有
    HTTP 响应可言，只能"不 accept"）——两个面**判定同一份**，只是失败的表达方式不同。

    - `local` 且未配 token ⇒ 放行（本地开发零摩擦，与旧 B6 行为逐字一致）；
    - 其余情形 ⇒ 必须匹配。

    ⚠️ **空 token 必须判"不通过"**：`shared` 姿态下 `api_token` 为空时，
    若沿用"空即放行"的写法就会 fail open。这里用「`not expected` 也返回 False」堵死，
    使运行期不会因为绕过启动校验而失守。
    """
    if not settings.auth_required:
        return True
    expected = settings.api_token
    return bool(expected) and hmac.compare_digest((provided or "").encode(), expected.encode())


def check_api_token(provided: str | None) -> None:
    """HTTP 面的判定：`is_api_token_valid` 为假即 401（判定只有一份，见上）。"""
    if not is_api_token_valid(provided):
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED_DETAIL)


class AgentPromotionTokenError(ValueError):
    """Domain-level promotion authority failure; HTTP is only one transport."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


def validate_agent_promotion_token(provided: str | None) -> str:
    """Pure IMP-052 promotion credential validator shared by all call paths.

    This is same-process privilege separation, not user/OS-principal authentication: trusted code
    able to read server configuration remains inside the trust boundary.
    """
    expected = (settings.agent_promotion_token or "").strip()
    ordinary = (settings.api_token or "").strip()
    if not expected:
        raise AgentPromotionTokenError("disabled", "参数晋级批准入口未启用：ASHARE_AGENT_PROMOTION_TOKEN 未配置")
    if len(expected) < 32:
        raise AgentPromotionTokenError("weak", "参数晋级批准凭据至少需要 32 字符")
    if ordinary and hmac.compare_digest(expected, ordinary):
        raise AgentPromotionTokenError("not_separate", "参数晋级批准凭据不得与普通 API token 共用")
    supplied = provided or ""
    if not hmac.compare_digest(supplied, expected):
        raise AgentPromotionTokenError("invalid", "参数晋级批准凭据无效")
    return expected


def check_agent_promotion_token(provided: str | None) -> str:
    """HTTP adapter for :func:`validate_agent_promotion_token`."""
    try:
        return validate_agent_promotion_token(provided)
    except AgentPromotionTokenError as exc:
        raise HTTPException(status_code=403 if exc.code == "invalid" else 503, detail=exc.detail) from exc


def ws_encode_token(token: str) -> str:
    """把 token 编成子协议后缀（base64url、去填充）——前端 `lib/ws-token.ts` 的同构实现。"""
    return base64.urlsafe_b64encode(token.encode("utf-8")).decode("ascii").rstrip("=")


def ws_token_subprotocol(raw: str | None) -> str | None:
    """从 `Sec-WebSocket-Protocol` **原文**里挑出承载凭据的那个子协议，返回**其原文**。

    返回 `ashare-token.<b64url>` 整串（而不是解出的 token），是因为调用方必须把
    它**原样回显**给客户端——理由见 `ws_decode_subprotocol_token` 的"必须回显"说明。

    `raw` 是逗号分隔的多个子协议（客户端可能只带我们的一个，也可能与别的子协议并列），
    故逐个试；找不到返回 None（调用方据此决定"不回显"）。
    """
    for proto in (raw or "").split(","):
        proto = proto.strip()
        if proto.startswith(WS_SUBPROTOCOL_PREFIX) and proto[len(WS_SUBPROTOCOL_PREFIX):]:
            return proto
    return None


def ws_decode_subprotocol_token(proto: str | None) -> str | None:
    """把 `ashare-token.<base64url>` 解回 token；非法 base64 / 非 UTF-8 返回 None。

    ⚠️ **为什么必须原样回显**（`websocket/routes.py` 据此传 `accept(subprotocol=...)`）：
    RFC 6455 §4.1 与 WHATWG HTML 的建连算法都规定——客户端提议了子协议、而服务端
    响应里**一个客户端提议过的子协议都没有**时，客户端必须**主动判定连接失败**
    （Chrome/Firefox 与 `ws` 均实现该检查，报 "Server sent no subprotocol"）。
    ⇒ 只要客户端带了子协议，服务端就**必须选一个它提议过的**回显，否则连接根本建不起来。
    这条约束也顺带解释了"由反代单方面注入子协议"为何行不通：注入的值客户端没提议过，
    回显它同样会被客户端拒绝。
    """
    prefix_len = len(WS_SUBPROTOCOL_PREFIX)
    if not proto or not proto.startswith(WS_SUBPROTOCOL_PREFIX):
        return None
    payload = proto[prefix_len:]
    if not payload:
        return None
    padded = payload + "=" * (-len(payload) % 4)
    try:
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return None
