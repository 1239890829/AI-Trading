"""入站凭据校验的**单点策略**（B6 写鉴权 → R22 统一鉴权边界）。

## 为什么要把判定抽出来

原先判定只有一处（`api/deps.py::require_write_token`），但**消费面正要变宽**：
写接口之外还要盖住「敏感读 / 花钱读」与 WebSocket。若每个面各写一份判定，
两份就会漂移——而**鉴权判定的漂移是静默的**（多一处宽松分支 = 多一个无声后门，
运行时与"正确"长得完全一样）。故：**策略只有这一份**，各面只做「把凭据取出来」这件事
（HTTP 取请求头、WebSocket 取子协议），取法不同、判定同一。

## 三种面、一个判定

| 面 | 凭据通道 | 适配位置 |
|---|---|---|
| **全部 HTTP 路由**（默认拒绝，豁免见下） | `X-API-Token` 请求头 | `api/deps.py::require_api_token`（router 级挂载） |
| 写接口（非 GET） | 同上（**同判定**，只是显式作用域标记） | `api/deps.py::require_write_token` |
| WebSocket `/ws/quotes` | `Sec-WebSocket-Protocol` 子协议 | `websocket/routes.py` |

「全部 HTTP 路由」与「写接口」**不是两套规则**：两者都调 `check_api_token`，
判定只有一份；保留两个名字是因为前者是"地板"（漏挂即 401），后者是"这条路由会改状态"
的声明式标记（既有 `test_write_token.py` 靠它做机械门禁）。

⚠️ **WebSocket 为何不能也用请求头**：浏览器的 `WebSocket` 构造器**不允许设置自定义
请求头**（不是本项目的选择，是平台约束）⇒ 只能走 URL 或子协议。URL 通道（`?token=`）
已被本项目**刻意关闭**（2026-09-14：token 出现在 URL 上会被浏览器历史、`Referer`、
反代访问日志逐层留存）。子协议是**请求头**，不进 URL，故是三条通道里唯一
既可用又符合既有决策的（`Sec-WebSocket-Protocol` 承载凭据亦是 Kubernetes
等项目的既有做法）。

## 子协议为何要 base64url 编码

RFC 6455 的子协议值必须是 **HTTP token**（字符集不含 `=` `/` `+` 等）。
用户配的 token 若是 base64（含 `=` 填充）或含其它字符，直接拼进子协议会让浏览器
在 `new WebSocket(...)` 处**抛异常**——而那是**前端**的失败，后端只会看到
"连接没建立"，属于最难排查的一类。故两侧统一 `base64url(token)`（去填充）：
字符集恒为 `A-Za-z0-9-_`，对任意二进制安全，且顺带让明文 token 不出现在握手中。

## WS 凭据怎么到达浏览器（唯一一处"凭据出服务端"）

HTTP 面浏览器**从不持有** token（由 Next 服务端代理在运行时注入请求头）。WS 面做不到
这一点：回显约束（见下）要求"客户端提议过才能回显"⇒ **浏览器必须自己知道凭据**，
且它是**浏览器内存**里的值，不是构建产物里的常量。故投递路径刻意设计为：

    浏览器 ──GET /api/ws-credential──> Next Route Handler（运行时读 ASHARE_API_TOKEN）
                                          └─> 返回 `ashare-token.<base64url>`

三重约束保证它不比 HTTP 面更弱：① 端点在同源 Next 服务端，**构建期不内联**任何秘密
（`NEXT_PUBLIC_*` 禁令由 `apps/web/lib/env-secrecy.test.ts` 机械守卫）；② 返回的是
**编码后的子协议**而非明文 token，且不进 URL、不进浏览器历史 / Referer / 反代日志；
③ 凭据只在**内存**中持有，不落 localStorage / cookie。

⚠️ **残留风险（必须知道，别当成"已解决"）**：能打开前端的访客可以取得该凭据，
进而**绕过前端直连后端端口**。所以它保护的是"后端端口不对未授权者开放"，
**不是"区分前端访客的身份"**——真正区分用户需要会话/身份层，那是 R22 明确
"不要求现在上"的部分。配套纪律：后端端口仍须只绑回环（`docker-compose.prod.yml`
已如此），共享部署把反代作为唯一入口。

## 保护面：默认拒绝，而不是「敏感清单」

`shared` 姿态下**所有** HTTP 路由都要凭据，例外只有一份**显式登记**的清单
（见 `AUTH_EXEMPT_PATHS`）。刻意不用「敏感读白名单」的写法：白名单是
**默认放行 + 逐条登记才保护**，于是「新加了会花钱/读持仓的端点但忘了登记」
与「登记好了」在运行时**长得一模一样**（都是 200，只有配了 token 才分叉）
——这正是 R22 病灶的形态（原实现只按"写接口"分类，于是助手聊天与摘要
这类花钱读被整类漏掉）。默认拒绝把这个失效方向**翻转**：忘登记的结果是
该端点**报 401**（吵闹、立刻被发现），而不是**静默开放**。

代价是新增端点必须先确认「它确实该受保护」——这是可接受的，因为答案
99% 是"是"；而唯一需要豁免的（存活探针）已在清单里显式登记并有测试钉住。

## 失败方向

一律 **fail closed**：`shared` 姿态下 token 为空时，运行期判定**拒绝**（而不是放行）。
启动校验本应拦住这种配置，但**不能把启动校验当作唯一防线**——它只覆盖
"经 `main.py` 启动"这一条路径（测试夹具、脚本直连、ASGI 服务器自定义入口都可能绕过）。
"""

from __future__ import annotations

import base64
import binascii

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
    return bool(expected) and provided == expected


def check_api_token(provided: str | None) -> None:
    """HTTP 面的判定：`is_api_token_valid` 为假即 401（判定只有一份，见上）。"""
    if not is_api_token_valid(provided):
        raise HTTPException(status_code=401, detail=_UNAUTHORIZED_DETAIL)


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
