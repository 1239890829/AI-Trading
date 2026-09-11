"""助手「认知缺口」自曝检测（2026-09-11）。

## 解决什么
用户实测：系统里明明有的数据，助手答「我没有该股的分时明细、资金流向和龙虎榜数据」。
根因是提示词自我否认 + 工具覆盖缺口（见同日 prompt.py / tools.py 的修复）。
但那类问题**不会只犯一次**——以后新增数据源，助手照样可能不知道。

## 做法
不需要用户投诉，也不需要人肉巡检：**在服务端把"自我否认"这个信号记下来**。
当回答里出现「我没有/取不到 + 数据类名词」，而本轮**一次工具都没调用**时，
大概率不是真的没有，而是工具没覆盖到 / 提示词没说清——两种都该被修。
日志里累积的每一条，就是一份**由系统自己产出的认知缺口清单**。

## 边界（宁缺勿滥）
- 本轮调用过工具 → 直接放过：模型已经尽力取数，取不到是数据源的事，不是认知缺口；
- 只在工具启用时检测：关掉工具时"我没有数据"本来就是对的；
- 命中只是 `warning` 级留痕，**绝不改答案、不阻断**——它是信号，不是判据。
"""
from __future__ import annotations

# 「声称自己没有」的表达
DENIAL_MARKERS: tuple[str, ...] = (
    "我没有",
    "我无法获取",
    "无法获取",
    "取不到",
    "没有权限",
    "不具备",
    "暂时没有",
)

# 只有在同一窗口里出现数据类名词时才算"数据缺口"，避免把
# "我没有卖出建议/我不具备投顾资质"这类正当声明误判成缺口。
DATA_HINTS: tuple[str, ...] = (
    "数据", "行情", "资金", "龙虎榜", "分时", "K线", "K 线",
    "公告", "财务", "指数", "持仓", "自选", "涨停", "板块", "题材",
)

WINDOW = 40
LOOKBEHIND = 20


def looks_like_false_denial(text: str, *, tools_used: list[str] | None = None) -> bool:
    """回答疑似 "系统有、却说自己没有"。纯函数，便于单测。

    取**以标记词为中心**的窗口（前 20 字 + 后 40 字）：中文的宾语常在动词前
    （"这部分行情我无法获取"——"行情"在"我无法获取"之前），只向后取窗口会漏掉。
    """
    if tools_used:
        return False
    t = text or ""
    if not t:
        return False
    for marker in DENIAL_MARKERS:
        start = t.find(marker)
        while start >= 0:
            window = t[max(0, start - LOOKBEHIND):start + WINDOW]
            if any(h in window for h in DATA_HINTS):
                return True
            start = t.find(marker, start + 1)
    return False


def describe_gap(answer: str, question: str = "") -> str:
    """给日志用的一行摘要（截断，避免把整段回答写进日志）。"""
    head = " ".join((question or "").split())[:60]
    tail = " ".join((answer or "").split())[:120]
    return f"q={head!r} answer={tail!r}"
