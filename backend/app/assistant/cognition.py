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

import json
import logging
from pathlib import Path

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

log = logging.getLogger(__name__)

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


# ---------------------------------------------------------------- 缺口台账

#: 缺口留痕文件（JSONL 追加写；与事件存储同风格，便于事后统计而不必翻日志）
GAP_LOG_PATH = Path(__file__).resolve().parents[2] / "data" / "cognition_gaps.jsonl"
#: 单次读取的条数上限（防止文件长期增长后一次读入过多）
GAP_READ_LIMIT = 50


def record_gap(answer: str, question: str = "") -> dict | None:
    """把一条认知缺口**落到台账**（KW-ENG-49："日志即自动产出的缺口清单"）。

    此前只有 `log.warning` —— 日志会滚动、没人聚合，"清单"实际上**没有消费方**
    （典型的产出即死）。落盘后可被议程/复盘读取，形成闭环。

    :returns: 写入的记录；写入失败返回 None（**留痕失败不得影响回答**）。
    """
    from app.core.bjtime import beijing_now_naive

    rec = {
        "at": beijing_now_naive().isoformat(timespec="seconds"),
        "question": " ".join((question or "").split())[:120],
        "answer": " ".join((answer or "").split())[:200],
    }
    try:
        GAP_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with GAP_LOG_PATH.open("a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001 —— 留痕是旁路，失败只记日志
        log.warning("认知缺口留痕失败（不影响回答）")
        return None
    return rec


def recent_gaps(limit: int = GAP_READ_LIMIT) -> list[dict]:
    """读最近 N 条认知缺口（新在后）。缺失/损坏返回空列表（不是错误）。"""
    if not GAP_LOG_PATH.exists():
        return []
    try:
        lines = GAP_LOG_PATH.read_text(encoding="utf-8").splitlines()
    except Exception:  # noqa: BLE001
        return []
    out: list[dict] = []
    for ln in lines[-max(1, limit):]:
        try:
            rec = json.loads(ln)
        except Exception:  # noqa: BLE001 —— 单行损坏跳过，不因一行坏掉全表
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out
