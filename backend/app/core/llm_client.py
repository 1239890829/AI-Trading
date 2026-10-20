"""OpenAI 兼容 chat/completions 最小客户端 + claude_cli 无头网关。

`LLMAnalyzer` / `LLMSummarizer` 共用。刻意只用**同步**实现——
两个调用点（review service、news 路由）都在事件循环里，同步实现
由调用方 `asyncio.to_thread` 包裹，不在本模块内引入 async 双轨。

两种后端（`chat_completion` 的 provider 参数二选一）：
- openai（默认）：HTTP 直连 OpenAI 兼容 /chat/completions 端点
- claude_cli：子进程调本机 `claude -p` 无头模式——LLM 凭据与网络
  都由用户自己的 Claude Code 配置承担（适用于只有客户端受限中转
  key 的场景，如 AgentRouter 的 WAF 只放行官方 CLI）。关工具 +
  裁剪系统提示后单次调用 ~100 input tokens。

只封装「发请求 + 解析回复」这一层；提示词构造、结果校验、失败降级
都留在各自分析器里——那是业务语义，混进来会让两边的契约看不清。
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import threading
from time import monotonic
from collections import OrderedDict
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx

log = logging.getLogger(__name__)

# Bounded, process-local transport observations. No prompts, responses or credentials.
_cli_health: OrderedDict[tuple[str, str], dict[str, Any]] = OrderedDict()
_cli_health_lock = threading.Lock()


def cli_health_observation(model: str, cli_path: str) -> dict[str, Any] | None:
    """Return a copy for this exact model/executable; other targets cannot prove health."""
    key = (model, resolve_cli_path(cli_path) or cli_path)
    with _cli_health_lock:
        value = _cli_health.get(key)
        return dict(value) if value is not None else None


def _record_cli_health(model: str, cli_path: str, *, ok: bool) -> None:
    try:
        from app.core.bjtime import beijing_now

        key = (model, resolve_cli_path(cli_path) or cli_path)
        with _cli_health_lock:
            _cli_health[key] = {
                "ok": ok, "epoch": monotonic(),
                "at": beijing_now().isoformat(timespec="seconds"),
            }
            _cli_health.move_to_end(key)
            while len(_cli_health) > 16:
                _cli_health.popitem(last=False)
    except Exception as exc:  # observational metadata cannot change business success/failure
        log.debug("CLI health observation failed: %s", type(exc).__name__)


class LLMFailure(str, Enum):
    """LLM 失败分类——降级、日志与告警按此分流，不靠解析消息字符串。

    `QUOTA` 与 `GATEWAY_ERROR` 的区分是这条枚举存在的主因：两者在系统里
    都表现为「LLM 不可用 → 降级到 rules」，但处置完全不同——QUOTA 要充值
    （用户动作），GATEWAY_ERROR 只能等网关恢复（运维动作）。2026-09-06 前
    两者混为一谈，只能靠人肉翻日志发现。
    """

    NOT_CONFIGURED = "not_configured"  # 缺 base_url/api_key/model
    UNAVAILABLE = "unavailable"        # CLI 找不到 / 启动失败 / OSError
    TIMEOUT = "timeout"                # 子进程或 HTTP 超时
    QUOTA = "quota"                    # 网关余额不足（403 need quota / 402 / 429）
    GATEWAY_ERROR = "gateway_error"    # 网关侧失败（api_error / 5xx / 非 2xx）
    BAD_RESPONSE = "bad_response"      # 非 JSON / 结构不对
    EMPTY = "empty"                    # 网关成功但回复为空
    BAD_REQUEST = "bad_request"        # 调用方参数问题（缺 user 消息等）


class LLMError(RuntimeError):
    """LLM 调用或解析失败。上抛后由 ModelRouter / SummaryRouter 降级。

    带 `kind`（`LLMFailure`）——消费方据此区分「要充值」与「等恢复」，
    不要去 match 消息文本（网关文案会变）。
    """

    def __init__(self, message: str, kind: "LLMFailure" = LLMFailure.GATEWAY_ERROR) -> None:
        super().__init__(message)
        self.kind = kind


# 网关余额不足的文案标记（小写匹配）。刻意不含裸 "403"——网关 WAF 的
# `401 unauthorized client detected` / 403 是鉴权问题，不是额度问题。
_QUOTA_MARKERS = (
    "__MUTANT__",
    "need quota",
    "quota is not enough",
    "token quota",
    "insufficient",
    "insufficient_balance",
    "insufficient balance",
    "payment required",
    "balance is not enough",
    "out of balance",
    "余额不足",
    "额度不足",
)


def classify_text_failure(text: str, *, default: LLMFailure = LLMFailure.GATEWAY_ERROR) -> LLMFailure:
    """从网关返回的文本里判失败类型：命中额度标记即 QUOTA，否则回落 default。"""
    low = (text or "").lower()
    if any(m in low for m in _QUOTA_MARKERS):
        return LLMFailure.QUOTA
    return default


def classify_cli_failure(returncode: int, stdout: str = "", stderr: str = "") -> LLMFailure:
    """claude_cli 失败分类：额度文案优先，其次按退出码兜底。

    注意 stdout 也要参与匹配——CLI 把网关错误塞进 JSON 的 `result` 字段
    （如 `token quota is not enough`），只看 stderr 会漏判成 GATEWAY_ERROR。
    """
    return classify_text_failure(f"{stdout}\n{stderr}", default=LLMFailure.GATEWAY_ERROR)


def classify_http_failure(status: int, body: str = "") -> LLMFailure:
    """OpenAI 兼容 HTTP 失败分类：402/429 归额度，其余非 2xx 归网关。"""
    if status in (402, 429):
        return LLMFailure.QUOTA
    kind = classify_text_failure(body, default=LLMFailure.GATEWAY_ERROR)
    if kind is not LLMFailure.GATEWAY_ERROR:
        return kind
    return LLMFailure.GATEWAY_ERROR


# 失败分类 → 人话提示。UI 与告警文案直接消费，别再各自拼一遍——
# 关键是 quota 的提示必须指向"充值"，其余不许出现"充值"二字（否则误导用户）。
_KIND_HINTS: dict[str, str] = {
    LLMFailure.QUOTA.value: "网关额度不足，需给网关账户充值",
    LLMFailure.GATEWAY_ERROR.value: "网关侧失败，等其恢复（非本项目可修）",
    LLMFailure.TIMEOUT.value: "调用超时（网关慢或 CLI 冷启动过久）",
    LLMFailure.UNAVAILABLE.value: "claude CLI 不可用（未安装或路径不对）",
    LLMFailure.NOT_CONFIGURED.value: "未配置 LLM 模型/后端",
    LLMFailure.EMPTY.value: "网关返回空回复",
    LLMFailure.BAD_RESPONSE.value: "网关响应结构异常",
    LLMFailure.BAD_REQUEST.value: "请求构造有误",
}


def hint_for(kind: str | None) -> str | None:
    """失败分类 → 人话提示；未知/无失败返回 None（三态：None ≠ 空串）。"""
    return _KIND_HINTS.get(kind or "")


def _endpoint(base_url: str) -> str:
    """兼容 base_url 带/不带尾斜杠、带/不带 /v1 的写法——只拼 path。"""
    return base_url.rstrip("/") + "/chat/completions"


def resolve_cli_path(explicit: str = "") -> str | None:
    """解析 claude CLI 可执行路径：显式配置 > PATH > nvm 安装目录。

    找不到返回 None（调用方据此判定 claude_cli 后端不可用），绝不抛异常——
    is_available 语义要求「判不出 = 不可用」而非崩溃。
    """
    if explicit.strip():
        p = Path(explicit.strip()).expanduser()
        return str(p) if p.is_file() else None
    which = shutil.which("claude")
    if which:
        return which
    # 沙箱/非交互 shell 常不带 nvm PATH，兜底扫 nvm 各版本取最新
    candidates = sorted(Path.home().glob(".nvm/versions/node/*/bin/claude"))
    return str(candidates[-1]) if candidates else None


def _normalized_usage(raw: Any) -> dict[str, int] | None:
    """Normalize OpenAI/Claude-style token usage; missing/partial usage stays unknown."""
    if not isinstance(raw, dict):
        return None
    inp = raw.get("input_tokens", raw.get("prompt_tokens"))
    out = raw.get("output_tokens", raw.get("completion_tokens"))
    try:
        inp_i = int(inp) if inp is not None else None
        out_i = int(out) if out is not None else None
    except (TypeError, ValueError):
        return None
    if inp_i is None or out_i is None or inp_i < 0 or out_i < 0:
        return None
    return {"input_tokens": inp_i, "output_tokens": out_i}


def _emit_usage(callback: Callable[[dict[str, int] | None], None] | None, raw: Any) -> None:
    if callback is None:
        return
    try:
        callback(_normalized_usage(raw))
    except Exception as exc:  # telemetry callback must not change model success semantics
        log.debug("LLM usage callback failed: %s", type(exc).__name__)


def chat_completion_via_cli(
    model: str,
    messages: list[dict[str, str]],
    *,
    cli_path: str = "",
    timeout: float = 120.0,
    usage_callback: Callable[[dict[str, int] | None], None] | None = None,
    observe_health: bool = True,
    env: dict[str, str] | None = None,
) -> str:
    """Stateless CLI completion; optional transport observation never changes its result."""
    try:
        content = _chat_completion_via_cli(
            model, messages, cli_path=cli_path, timeout=timeout,
            usage_callback=usage_callback, env=env,
        )
    except LLMError:
        if observe_health:
            _record_cli_health(model, cli_path, ok=False)
        raise
    if observe_health:
        _record_cli_health(model, cli_path, ok=True)
    return content


def _chat_completion_via_cli(
    model: str,
    messages: list[dict[str, str]],
    *,
    cli_path: str = "",
    timeout: float = 120.0,
    usage_callback: Callable[[dict[str, int] | None], None] | None = None,
    env: dict[str, str] | None = None,
) -> str:
    """子进程调 `claude -p` 无头模式，返回最终回复文本。

    messages 只支持现有两个消费方的单轮形态：system → --system-prompt，
    其余角色拼接为 prompt。`--tools ""` 关掉全部工具 + 裁剪动态系统提示，
    否则 Claude Code 自带 agent 提示词会把单次调用撑到 ~18k input tokens。
    凭据来自用户 ~/.claude 配置，本模块不读不存任何 key。
    """
    cli = resolve_cli_path(cli_path)
    if not cli:
        raise LLMError(
            "claude_cli 后端不可用：未找到 claude 可执行文件", LLMFailure.UNAVAILABLE,
        )
    system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    prompt = "\n\n".join(
        m["content"] for m in messages if m.get("role") != "system"
    ).strip()
    if not prompt:
        raise LLMError("claude_cli 调用缺少 user 消息", LLMFailure.BAD_REQUEST)
    cmd = [
        cli, "-p", prompt,
        "--output-format", "json",
        "--model", model,
        "--tools", "",
        "--no-session-persistence",
        "--exclude-dynamic-system-prompt-sections",
    ]
    if system:
        cmd += ["--system-prompt", system]
    try:
        options = {"env": env} if env is not None else {}
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False, **options,
        )
    except subprocess.TimeoutExpired:
        # TimeoutExpired.__str__ includes the complete argv, including all input
        # and the system prompt. Keep only model/time metadata, also in traceback.
        raise LLMError(
            f"claude_cli 调用超时（model={model}，限时={timeout:g}s），未取得完整输出；"
            "仅凭超时不能确定网关故障或模型不可用；可核对 ASHARE_REVIEW_LLM_MODEL 配置和网关状态",
            LLMFailure.TIMEOUT,
        ) from None
    except OSError as exc:
        raise LLMError(f"claude_cli 启动失败：{exc}", LLMFailure.UNAVAILABLE) from exc
    if proc.returncode != 0:
        kind = classify_cli_failure(proc.returncode, proc.stdout, proc.stderr)
        raise LLMError(
            f"claude_cli 退出码 {proc.returncode}（model={model}）："
            f"{(proc.stderr or proc.stdout)[:200]}",
            kind,
        )
    try:
        body = json.loads(proc.stdout)
    except ValueError as exc:
        raise LLMError("claude_cli 输出不是 JSON", LLMFailure.BAD_RESPONSE) from exc
    if body.get("is_error"):
        kind = classify_cli_failure(proc.returncode, proc.stdout, proc.stderr)
        raise LLMError(
            f"claude_cli 返回错误（model={model}）：{str(body.get('result'))[:200]}", kind,
        )
    content = body.get("result")
    if not isinstance(content, str) or not content.strip():
        raise LLMError("claude_cli 回复为空", LLMFailure.EMPTY)
    _warn_if_model_unrecognized(proc.stderr, model)
    _emit_usage(usage_callback, body.get("usage"))
    return content


def _warn_if_model_unrecognized(stderr: str, model: str) -> None:
    """CLI 报 `unrecognized_model` 时留一条日志——**它不是失败信号，但也不是噪音**。

    实测（2026-09-16）：该警告与调用成败**正交**——`deepseek-v4-flash` 带此警告
    仍然 3.3s 正常返回；而 `glm-5.3` 带同一警告则挂起至超时。也就是说它只说明
    「该名字不在 CLI 内置别名表里、被原样透传给网关」，**真正决定可用性的是上游**。
    ⇒ 历史文档把它单方面写成"无害警告"是不完整的（09-04 那次它伴随退出码 1、
    LLM 通道全天不可用）；这里保留可见性，避免下次又靠人肉分辨。
    """
    if stderr and "unrecognized_model" in stderr:
        log.warning(
            "claude_cli 未识别模型名 %r（已透传给上游网关）；"
            "是否可用取决于上游是否仍提供该模型", model,
        )


def chat_completion(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    timeout: float = 30.0,
    client: httpx.Client | None = None,
    provider: str = "openai",
    cli_path: str = "",
    usage_callback: Callable[[dict[str, int] | None], None] | None = None,
) -> str:
    """按 provider 分发调用，返回回复文本。

    openai 路径：任何失败（配置缺失 / 网络 / 非 200 / 响应结构不对 /
    空回复）都抛 `LLMError`，由路由层统一降级——绝不返回 None 或空串
    冒充成功。`client` 供测试注入 MockTransport；生产路径自建短连接。
    claude_cli 路径：CLI 冷启动 + 大 prompt 下 30s 不够，超时下限抬到
    120s（调用方传更大值则以调用方为准）。
    """
    if provider == "claude_cli":
        return chat_completion_via_cli(
            model, messages, cli_path=cli_path, timeout=max(timeout, 120.0),
            usage_callback=usage_callback,
        )
    if not (base_url and api_key and model):
        raise LLMError("LLM 未配置 base_url/api_key/model", LLMFailure.NOT_CONFIGURED)
    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    headers = {"Authorization": f"Bearer {api_key}"}
    try:
        if client is not None:
            resp = client.post(
                _endpoint(base_url), json=payload, headers=headers,
                timeout=timeout,
            )
        else:
            with httpx.Client(timeout=timeout) as hc:
                resp = hc.post(_endpoint(base_url), json=payload, headers=headers)
    except httpx.TimeoutException as exc:
        raise LLMError(f"LLM 请求超时：{exc}", LLMFailure.TIMEOUT) from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"LLM 请求失败：{exc}", LLMFailure.GATEWAY_ERROR) from exc
    if resp.status_code != 200:
        raise LLMError(
            f"LLM HTTP {resp.status_code}",
            classify_http_failure(resp.status_code),
        )
    try:
        body = resp.json()
    except ValueError as exc:
        raise LLMError("LLM 响应不是 JSON", LLMFailure.BAD_RESPONSE) from exc
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(
            f"LLM 响应缺少 choices/message/content：{body}", LLMFailure.BAD_RESPONSE,
        ) from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM 回复为空", LLMFailure.EMPTY)
    _emit_usage(usage_callback, body.get("usage"))
    return content


# Preserve only the existing CLI's user authentication and execution essentials.
# In particular, ASHARE_*, PYTHONPATH and unrelated service/account credentials
# must not reach even the temporary Python adapter process.
_BOUNDED_CLI_ENV = frozenset({
    "HOME", "PATH", "LANG", "LC_ALL", "LC_CTYPE", "LC_MESSAGES", "TZ",
    "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "WINDIR", "PATHEXT",
    "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_BASE_URL",
    "CLAUDE_CODE_OAUTH_TOKEN", "CLAUDE_CONFIG_DIR",
})


def bounded_chat_completion(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    provider: str,
    cli_path: str = "",
    timeout: float = 180.0,
    usage_callback: Callable[[dict[str, int] | None], None] | None = None,
) -> str:
    """One existing-model call with no project discovery or persistent CLI session.

    The CLI adapter changes only its child process's cwd. Its input environment
    is filtered before Python starts; the parent ASGI cwd/environment never
    changes. This is a transport boundary, not an operating-system sandbox or a
    semantic verifier. Non-CLI providers retain the existing HTTP transport.
    """
    try:
        if provider != "claude_cli":
            return chat_completion(
                base_url, api_key, model, messages, provider=provider,
                cli_path=cli_path, timeout=timeout, usage_callback=usage_callback,
            )
        cli = resolve_cli_path(cli_path)
        if not cli:
            raise LLMError("受限 CLI 不可用", LLMFailure.UNAVAILABLE)
        cli = str(Path(cli).resolve())
        child_env = {key: value for key, value in os.environ.items() if key in _BOUNDED_CLI_ENV}
        with tempfile.TemporaryDirectory(prefix="ashare-bounded-llm-") as temporary:
            directory = Path(temporary)
            adapter = directory / "bounded-cli"
            flags = [
                "--setting-sources", "user", "--settings", '{"disableAllHooks":true}',
                "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
                "--disable-slash-commands", "--max-turns", "1",
            ]
            adapter.write_text(
                f"#!{sys.executable}\n"
                "import os, sys\n"
                f"os.chdir({str(directory)!r})\n"
                f"cmd = [{cli!r}] + sys.argv[1:] + {flags!r}\n"
                f"os.execv({cli!r}, cmd)\n",
                encoding="utf-8",
            )
            adapter.chmod(0o700)
            return chat_completion_via_cli(
                model, messages, cli_path=str(adapter), timeout=timeout,
                usage_callback=usage_callback, observe_health=False, env=child_env,
            )
    except LLMError as exc:
        # Existing transports can include stdout, body, argv or upstream text in
        # their exception. Preserve its classification without forwarding text.
        raise LLMError(f"受限模型调用失败（{exc.kind.value}）", exc.kind) from None
    except OSError:
        raise LLMError("受限模型调用无法启动", LLMFailure.UNAVAILABLE) from None
    except Exception:
        raise LLMError("受限模型返回无法处理", LLMFailure.BAD_RESPONSE) from None


class ChatStream:
    """可关闭的流式补全：迭代产出文本增量，close() 幂等释放底层资源。

    close() 的存在是因为消费方（SSE 路由）要在客户端断开时终止底层
    传输——openai 路径关 httpx 响应，claude_cli 路径杀子进程。生成器
    阻塞在 stdout/网络读上时 Python 层无法中断，只有杀掉源头才有效。
    """

    def __init__(self, iterator: Iterator[str], closer: Callable[[], None]):
        self._it = iterator
        self._closer = closer
        self._closed = False

    def __iter__(self) -> Iterator[str]:
        return self

    def __next__(self) -> str:
        return next(self._it)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._closer()
        except Exception:  # noqa: BLE001  释放失败不影响主流程
            pass


def _stream_openai(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float,
    timeout: float,
) -> ChatStream:
    """OpenAI 兼容 SSE 流式。read 超时按「字节间隔」计，思维链长不会误杀。"""
    if not (base_url and api_key and model):
        raise LLMError("LLM 未配置 base_url/api_key/model", LLMFailure.NOT_CONFIGURED)
    client = httpx.Client(timeout=httpx.Timeout(timeout, read=max(timeout, 300.0)))
    req = client.build_request(
        "POST",
        _endpoint(base_url),
        json={"model": model, "messages": messages, "temperature": temperature, "stream": True},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        resp = client.send(req, stream=True)
    except httpx.TimeoutException as exc:
        client.close()
        raise LLMError(f"LLM 请求超时：{exc}", LLMFailure.TIMEOUT) from exc
    except httpx.HTTPError as exc:
        client.close()
        raise LLMError(f"LLM 请求失败：{exc}", LLMFailure.GATEWAY_ERROR) from exc
    if resp.status_code != 200:
        body = resp.read()[:200]
        resp.close()
        client.close()
        raise LLMError(
            f"LLM HTTP {resp.status_code}: {body!r}",
            classify_http_failure(resp.status_code, body.decode("utf-8", "replace")),
        )

    def _iter() -> Iterator[str]:
        try:
            for line in resp.iter_lines():
                line = line.strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    return
                try:
                    chunk = json.loads(payload)
                except ValueError:
                    continue
                try:
                    delta = chunk["choices"][0]["delta"].get("content")
                except (KeyError, IndexError, TypeError):
                    continue
                if isinstance(delta, str) and delta:
                    yield delta
        finally:
            resp.close()
            client.close()

    return ChatStream(_iter(), client.close)


def _stream_cli(
    model: str,
    messages: list[dict[str, str]],
    *,
    cli_path: str = "",
) -> ChatStream:
    """claude_cli 流式：stream-json + partial messages，逐 delta 产出。

    事件形态（实测 2.1.259）：
    - {"type":"stream_event","event":{"type":"content_block_delta",
      "delta":{"type":"text_delta","text":"…"}}}   ← 文本增量
    - {"type":"assistant","message":{...content 完整块...}}  ← 整段回包
    - {"type":"result","is_error":bool,"result":"..."}       ← 终态
    同时存在增量与整段时只认增量（saw_delta 防二次产出）。
    """
    cli = resolve_cli_path(cli_path)
    if not cli:
        raise LLMError(
            "claude_cli 后端不可用：未找到 claude 可执行文件", LLMFailure.UNAVAILABLE,
        )
    system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    prompt = "\n\n".join(
        m["content"] for m in messages if m.get("role") != "system"
    ).strip()
    if not prompt:
        raise LLMError("claude_cli 调用缺少 user 消息", LLMFailure.BAD_REQUEST)
    cmd = [
        cli, "-p", prompt,
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--model", model,
        "--tools", "",
        "--no-session-persistence",
        "--exclude-dynamic-system-prompt-sections",
    ]
    if system:
        cmd += ["--system-prompt", system]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except OSError as exc:
        raise LLMError(f"claude_cli 启动失败：{exc}", LLMFailure.UNAVAILABLE) from exc

    stderr_tail = [""]

    def _drain_stderr() -> None:
        if proc.stderr is None:
            return
        try:
            stderr_tail[0] = proc.stderr.read()[-300:]
        except Exception:  # noqa: BLE001
            pass

    import threading

    threading.Thread(target=_drain_stderr, daemon=True).start()

    def _close() -> None:
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)

    def _iter() -> Iterator[str]:
        saw_delta = False
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            etype = ev.get("type")
            if etype == "stream_event":
                inner = ev.get("event") or {}
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta") or {}
                    if delta.get("type") == "text_delta":
                        text = delta.get("text") or ""
                        if text:
                            saw_delta = True
                            yield text
            elif etype == "assistant" and not saw_delta:
                content = (ev.get("message") or {}).get("content") or []
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        text = block.get("text") or ""
                        if text:
                            yield text
            elif etype == "result":
                if ev.get("is_error"):
                    raise LLMError(
                        f"claude_cli 返回错误：{str(ev.get('result'))[:200]}",
                        classify_text_failure(str(ev.get("result"))),
                    )
                return
        # stdout 结束但没收到 result：子进程异常退出
        rc = proc.wait(timeout=10)
        if rc != 0:
            raise LLMError(
                f"claude_cli 退出码 {rc}：{stderr_tail[0][:200]}",
                classify_cli_failure(rc, "", stderr_tail[0]),
            )
        if not saw_delta:
            raise LLMError(
                f"claude_cli 回复为空：{stderr_tail[0][:200]}", LLMFailure.EMPTY,
            )

    return ChatStream(_iter(), _close)


def stream_chat_completion(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    timeout: float = 30.0,
    provider: str = "openai",
    cli_path: str = "",
) -> ChatStream:
    """按 provider 分发流式调用，返回 ChatStream。

    与 chat_completion 同参语义；失败同样抛 LLMError（openai 路径在
    建连/HTTP 状态时抛，claude_cli 路径延迟到迭代时——首个 delta 之前
    的错误都以 LLMError 形式从 next() 冒出，消费方按同一种异常处理）。
    """
    if provider == "claude_cli":
        return _stream_cli(model, messages, cli_path=cli_path)
    return _stream_openai(
        base_url, api_key, model, messages,
        temperature=temperature, timeout=timeout,
    )


def extract_json_object(text: str) -> dict[str, Any]:
    """从 LLM 回复中提取第一个 JSON 对象。

    容忍三类常见包裹：```json 围栏、前后说明文字、尾随杂讯。
    解析失败抛 ValueError——语义区别于 LLMError（网络层失败），
    对路由层来说两者都走降级，分类型只为日志可读。
    """
    s = text.strip()
    if s.startswith("```"):
        nl = s.find("\n")
        if nl != -1:
            s = s[nl + 1:]
    fence_end = s.rfind("```")
    if fence_end != -1:
        s = s[:fence_end]
    start, end = s.find("{"), s.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("LLM 回复中未找到 JSON 对象")
    try:
        obj = json.loads(s[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"LLM 回复 JSON 解析失败：{exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("LLM 回复 JSON 不是对象")
    return obj
