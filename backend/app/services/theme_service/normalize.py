"""题材标签归一化与封板时间解析（纯函数：归一化词典 / 封板档位 / 早封率 / 留存率）。

`backend/app/services/theme_service.py` 的内部切片（IMP-005 批 4，2026-09-15 从 1383 行单文件按业务域拆出）。
**只搬位置、不重写**：语句正文与前置注释与拆分前逐字符相同（唯一例外见文件内注释）；
对外仍由 `app.services.theme_service` 统一转发，因此引用方零改动。
"""
from __future__ import annotations

# ---------------------------------------------------------------- 题材归一化词典

#: 原始标签 → 归一化题材名。只合并明确同义的，避免过度合并掩盖真实结构。
#: 新增词条时必须同步 tests/test_theme_service.py，并说明合并理由。
THEME_ALIASES: dict[str, str] = {
    # ⚠️ 官方名称纪律（2026-09-08 用户指令）：题材名称必须逐字使用官方标准名，
    # 严禁自创/翻译/简写。本表只允许两类条目：
    #   1) value 是 **题材目录中真实存在的官方概念名**（如「黄金概念」「液冷服务器」
    #      「数据中心(AIDC)」「央企国企改革」「转基因」——逐字含括号）；
    #   2) 原始标签本身已在目录中 → 直接删除映射让其直通（如「业绩增长」「算力租赁」
    #      「人形机器人」「国企改革」等 ths 原发标签）。
    # 曾有 10 个自创目标名（业绩驱动/黄金珠宝/国资改革/液冷/算力/机器人/数据中心/
    # 重组/控制权变更/次新股/PTFE）被全部清除——它们在同花顺 App 里搜不到。
    # 官方无统一概念的近似标签（如业绩类 16 种表述）**不做归并**：名称准确性优先，
    # 碎片化由 official_matches 官方挂靠 + formation 分级兜底。
    # 黄金系：炒作时同涨同跌，归到官方「黄金概念」
    "黄金": "黄金概念",
    "黄金概念": "黄金概念",
    "珠宝加工": "黄金概念",
    "黄金租赁": "黄金概念",
    "艺术珠宝": "黄金概念",
    # 液冷：官方概念名是「液冷服务器」
    "AI液冷": "液冷服务器",
    # 数据中心：官方概念名逐字为「数据中心(AIDC)」
    "数据中心业务": "数据中心(AIDC)",
    "数据中心交换机": "数据中心(AIDC)",
    # 国资：官方概念名为「央企国企改革」
    "央国企改革": "央企国企改革",
    "央企改革": "央企国企改革",
    # 转基因：与种业同源，官方「转基因」概念存在
    "转基因玉米": "转基因",
}


#: 封板时间分档（用于判断封板质量与跟风程度）
SEAL_PHASES = [
    (0, "早盘"),      # < 10:00
    (100000, "上午"),  # 10:00 - 11:30
    (113000, "午后"),  # 13:00 - 14:00
    (140000, "尾盘"),  # >= 14:00
]


# ---------------------------------------------------------------- 纯函数：题材解析


def parse_theme_tags(reason: str | None) -> list[str]:
    """拆分 ths 涨停原因为题材标签列表。

    ths 口径是「+」分隔的多题材串，如 ``"黄金珠宝+珠宝加工+客户拓展"``。
    空值/无内容返回空列表（由调用方归入「未分类」），不猜。
    """
    if not reason:
        return []
    tags: list[str] = []
    for part in str(reason).split("+"):
        tag = part.strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def normalize_theme(tag: str) -> str:
    """把原始题材标签归一到规范名；未收录的原样返回。"""
    t = tag.strip()
    return THEME_ALIASES.get(t, t)


def seal_phase(first_seal_time: str | None) -> str | None:
    """封板时间档位：早盘 / 上午 / 午后 / 尾盘。

    输入形如 ``"09:33"`` 或 ``"093300"``；无法解析返回 None（不臆造档位）。
    """
    if not first_seal_time:
        return None
    digits = "".join(ch for ch in str(first_seal_time) if ch.isdigit())
    if len(digits) < 4:
        return None
    # "10:30" → "1030" 需补零成 HHMMSS 再比较，否则 1030 < 100000 会全部落进「早盘」
    hhmmss = int(digits.ljust(6, "0")[:6])
    phase = SEAL_PHASES[0][1]
    for threshold, label in SEAL_PHASES:
        if hhmmss >= threshold:
            phase = label
    return phase


def parse_hhmmss(ts: str | None) -> int | None:
    """``"09:33"`` / ``"093300"`` → HHMMSS 整数；无法解析 → None。

    2026-09-15 由 ``_parse_hhmmss`` 提升为公开名（行为零变化）：封板时间解析
    此后有两处消费方（本模块的早封率、`picks/tradability` 的开盘即涨停判定），
    私有名跨模块引用会诱发第二份实现——那正是"同一口径两套代码"的开端。
    """
    if not ts:
        return None
    digits = "".join(ch for ch in str(ts) if ch.isdigit())
    if len(digits) < 4:
        return None
    return int(digits.ljust(6, "0")[:6])


# 官方涨停情绪场景 12 口径：10:00 前首封视为「早封」
EARLY_SEAL_CUTOFF_HHMMSS = 100000


def early_seal_rate(hhmmss_values: list[int | None]) -> float | None:
    """题材早封率：首封时间 ≤10:00 的成员占比。

    时间缺失的成员从分母剔除（样本缺失≠非早封）；全部缺失 → None
    （三态纪律：没有样本不冒充 0%）。
    """
    valid = [v for v in hhmmss_values if v is not None]
    if not valid:
        return None
    return round(sum(1 for v in valid if v <= EARLY_SEAL_CUTOFF_HHMMSS) / len(valid), 4)


def seal_retention_rate(pairs: list[tuple[float | None, float | None]]) -> float | None:
    """题材封单留存：Σ当前封单 / Σ盘中最高封单（官方场景 12 口径）。

    成对参与：当前封单与最高封单**都非空**且 max>0 的成员才进聚合；
    无有效样本 → None。留存率≈1 封得实（收盘=全天最高），趋 0 说明
    尾盘炸板/撤单多。max_seal_money 仅 ths 主源提供（东财池无此字段），
    样本不足时显式 None，不冒充。盘中调用时当前封单是实时值，指标
    仅收盘口径有意义——消费方须标注。
    """
    num = den = 0.0
    for cur, mx in pairs:
        if cur is not None and mx is not None and mx > 0:
            num += cur
            den += mx
    if den <= 0:
        return None
    return round(num / den, 4)
