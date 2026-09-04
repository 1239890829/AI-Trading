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
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterator

import httpx


class LLMError(RuntimeError):
    """LLM 调用或解析失败。上抛后由 ModelRouter / SummaryRouter 降级。"""


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


def chat_completion_via_cli(
    model: str,
    messages: list[dict[str, str]],
    *,
    cli_path: str = "",
    timeout: float = 120.0,
) -> str:
    """子进程调 `claude -p` 无头模式，返回最终回复文本。

    messages 只支持现有两个消费方的单轮形态：system → --system-prompt，
    其余角色拼接为 prompt。`--tools ""` 关掉全部工具 + 裁剪动态系统提示，
    否则 Claude Code 自带 agent 提示词会把单次调用撑到 ~18k input tokens。
    凭据来自用户 ~/.claude 配置，本模块不读不存任何 key。
    """
    cli = resolve_cli_path(cli_path)
    if not cli:
        raise LLMError("claude_cli 后端不可用：未找到 claude 可执行文件")
    system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    prompt = "\n\n".join(
        m["content"] for m in messages if m.get("role") != "system"
    ).strip()
    if not prompt:
        raise LLMError("claude_cli 调用缺少 user 消息")
    cmd = [
        cli, "-p", prompt,
        "--output-format", "json",
        "--model", model,
        "--tools", "",
        "--exclude-dynamic-system-prompt-sections",
    ]
    if system:
        cmd += ["--system-prompt", system]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise LLMError(f"claude_cli 调用失败：{exc}") from exc
    if proc.returncode != 0:
        raise LLMError(
            f"claude_cli 退出码 {proc.returncode}：{(proc.stderr or proc.stdout)[:200]}"
        )
    try:
        body = json.loads(proc.stdout)
    except ValueError as exc:
        raise LLMError("claude_cli 输出不是 JSON") from exc
    if body.get("is_error"):
        raise LLMError(f"claude_cli 返回错误：{str(body.get('result'))[:200]}")
    content = body.get("result")
    if not isinstance(content, str) or not content.strip():
        raise LLMError("claude_cli 回复为空")
    return content


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
        )
    if not (base_url and api_key and model):
        raise LLMError("LLM 未配置 base_url/api_key/model")
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
    except httpx.HTTPError as exc:
        raise LLMError(f"LLM 请求失败：{exc}") from exc
    if resp.status_code != 200:
        raise LLMError(f"LLM HTTP {resp.status_code}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise LLMError("LLM 响应不是 JSON") from exc
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"LLM 响应缺少 choices/message/content：{body}") from exc
    if not isinstance(content, str) or not content.strip():
        raise LLMError("LLM 回复为空")
    return content


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
        raise LLMError("LLM 未配置 base_url/api_key/model")
    client = httpx.Client(timeout=httpx.Timeout(timeout, read=max(timeout, 300.0)))
    req = client.build_request(
        "POST",
        _endpoint(base_url),
        json={"model": model, "messages": messages, "temperature": temperature, "stream": True},
        headers={"Authorization": f"Bearer {api_key}"},
    )
    try:
        resp = client.send(req, stream=True)
    except httpx.HTTPError as exc:
        client.close()
        raise LLMError(f"LLM 请求失败：{exc}") from exc
    if resp.status_code != 200:
        body = resp.read()[:200]
        resp.close()
        client.close()
        raise LLMError(f"LLM HTTP {resp.status_code}: {body!r}")

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
        raise LLMError("claude_cli 后端不可用：未找到 claude 可执行文件")
    system = "\n".join(m["content"] for m in messages if m.get("role") == "system")
    prompt = "\n\n".join(
        m["content"] for m in messages if m.get("role") != "system"
    ).strip()
    if not prompt:
        raise LLMError("claude_cli 调用缺少 user 消息")
    cmd = [
        cli, "-p", prompt,
        "--output-format", "stream-json",
        "--include-partial-messages",
        "--verbose",
        "--model", model,
        "--tools", "",
        "--exclude-dynamic-system-prompt-sections",
    ]
    if system:
        cmd += ["--system-prompt", system]
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )
    except OSError as exc:
        raise LLMError(f"claude_cli 启动失败：{exc}") from exc

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
                    raise LLMError(f"claude_cli 返回错误：{str(ev.get('result'))[:200]}")
                return
        # stdout 结束但没收到 result：子进程异常退出
        rc = proc.wait(timeout=10)
        if rc != 0:
            raise LLMError(f"claude_cli 退出码 {rc}：{stderr_tail[0][:200]}")
        if not saw_delta:
            raise LLMError(f"claude_cli 回复为空：{stderr_tail[0][:200]}")

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
