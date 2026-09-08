"""飞书推送矩阵（2026-09-08 用户需求：明确触发时机与优先级）。

四类时机（优先级从高到低）：
1. CRITICAL 关键信号 —— 盘中实时：买点卡（buy_point 五因素，白名单内唯一的
   盘中股票信号类推送）
2. ANOMALY 系统异常 —— 交易时段每 15 分钟哨兵检查，状态变化才推：数据停更 /
   盘中零告警 / marketdb 停同步 / 日历损坏等**系统级**异常（非股票信号）
3. REPORT 复盘完成 —— 每日收盘后一次：复盘产出 + 进化议程执行结果摘要
4. SILENT 其余一切 —— watcher / 情绪 / LLM 探针等一律 in_app + 日志留痕，
   不推飞书（09-08 定稿纪律的成文化）

纪律继承：所有推送全中文（tri_text 单点收口）；不含确定性买卖结论。
"""
from __future__ import annotations

import logging
from enum import Enum

log = logging.getLogger(__name__)


class PolicyKind(str, Enum):
    CRITICAL = "critical"  # 买点卡（盘中实时）
    ANOMALY = "anomaly"    # 系统异常（盘中哨兵，状态变化才推）
    REPORT = "report"      # 复盘/议程完成（盘后一次）
    SILENT = "silent"      # 其余一切：不推飞书


#: 各类型是否允许进飞书实时通道
FEISHU_ALLOWED: dict[PolicyKind, bool] = {
    PolicyKind.CRITICAL: True,
    PolicyKind.ANOMALY: True,
    PolicyKind.REPORT: True,
    PolicyKind.SILENT: False,
}


def feishu_allowed(kind: PolicyKind) -> bool:
    """推送点在发飞书前必须过此检查：SILENT 类一律 in_app 留痕。"""
    return FEISHU_ALLOWED.get(kind, False)


class AnomalyPushGuard:
    """ANOMALY 类的「状态变化才推」守卫（进程内存态，重启清空可接受——
    重启后首轮会重推一次当前异常，符合「让用户知道现状」的语义）。"""

    def __init__(self) -> None:
        self._active: set[str] = set()

    def filter_new(self, issues: list[str]) -> list[str]:
        """返回新出现的异常（上次已推过的不重复）。"""
        fresh = [i for i in issues if i not in self._active]
        self._active = set(issues)
        return fresh
