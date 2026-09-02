"""OpenAI 兼容 chat/completions 最小客户端。

`LLMAnalyzer` / `LLMSummarizer` 共用。刻意只用**同步** httpx——
两个调用点（review service、news 路由）都在事件循环里，同步实现
由调用方 `asyncio.to_thread` 包裹，不在本模块内引入 async 双轨。

只封装「发请求 + 解析回复」这一层；提示词构造、结果校验、失败降级
都留在各自分析器里——那是业务语义，混进来会让两边的契约看不清。
"""
from __future__ import annotations

import json
from typing import Any

import httpx


class LLMError(RuntimeError):
    """LLM 调用或解析失败。上抛后由 ModelRouter / SummaryRouter 降级。"""


def _endpoint(base_url: str) -> str:
    """兼容 base_url 带/不带尾斜杠、带/不带 /v1 的写法——只拼 path。"""
    return base_url.rstrip("/") + "/chat/completions"


def chat_completion(
    base_url: str,
    api_key: str,
    model: str,
    messages: list[dict[str, str]],
    *,
    temperature: float = 0.2,
    timeout: float = 30.0,
    client: httpx.Client | None = None,
) -> str:
    """同步调用 OpenAI 兼容 /chat/completions，返回首条回复文本。

    任何失败（配置缺失 / 网络 / 非 200 / 响应结构不对 / 空回复）都抛
    `LLMError`，由路由层统一降级——绝不返回 None 或空串冒充成功。
    `client` 供测试注入 MockTransport；生产路径自建短连接。
    """
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
