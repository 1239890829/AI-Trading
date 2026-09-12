"""北京时间权威唯一性守卫（S2-8，2026-09-11）。

## 为什么需要它

收敛前全站有 **20 处** 重复的时区常量定义、**8 个** 各自实现的「当前北京时间」
函数，以及 4 处 `datetime.now(timezone.utc) + timedelta(hours=8)` 裸算术。
同一件事三十多种写法，后果不是「丑」，是**改不干净**：

- 2026-09-09 把 `alert.triggered_at` 统一成北京 naive 时，**写入侧改了、读取侧没改**——
  `assistant.py` / `assistant/tools.py` / `notifiers/feishu.py` 三处仍在 `+ timedelta(hours=8)`，
  于是飞书推送的触发时间晚 8 小时，助手「今日告警」在 16:00 之后**恒为空**
  （当天告警被推到次日而过滤掉）。同一天没有人发现，因为没有测试覆盖「口径改了」这件事。
- 2026-09-11 用户报「交易智能体很多功能时间不是北京时间」——agent 域 7 张表
  仍用 `utcnow`（UTC naive），前端 `new Date()` 把无时区标记的串按**本地时区**解析，
  等于原样显示 UTC 墙钟（早 8 小时）。

**本测试防的就是「下一次改口径又只改一半」**：只要还有人自己写时区常量、
自己实现 `beijing_now`，口径就必然再次分裂。

## 扫描规则（AST/文本混合，宁严勿松）

1. `app/` `tests/` `scripts/` 内不得出现 `timedelta(hours=8)` 形式的时区偏移
   —— 唯一豁免是 `app/core/bjtime.py` 本身，以及**断言**（`==` / `!=`）里对
   偏移值的校验（那是"验证权威值正确"，不是"自建权威值"）。
2. 除 `app/core/bjtime.py` 外，不得定义 `beijing_now` / `beijing_now_naive` /
   `beijing_today` / `to_beijing` / `to_beijing_naive`。
3. `app/market/trading_status.py` 与 `app/core/db.py` 不得再"重新拥有"时钟
   （前者曾导出 `beijing_now`、后者曾导出 `beijing_now_naive`）。
4. `app/` `scripts/` `tests/` 内禁止**任何** `.today()` 调用——不区分接收者是谁
   （`date.today()` / `date_cls.today()` / `pd.Timestamp.today()` 取的都是进程本地时区的今天）。
   日期归属一律 `beijing_today()`（见第 5 节）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
AUTHORITY = "app/core/bjtime.py"

#: 本文件自身豁免：它的 docstring 必须**叙述**被禁的写法（如「仍在 + timedelta(hours=8)」），
#: 不豁免就会被自己的说明文字误伤（同 test_import_lint 用 AST 而非文本匹配的动因）。
SELF = "tests/test_bjtime.py"

#: 权威模块独占的时钟函数名
CLOCK_NAMES = ("beijing_now", "beijing_now_naive", "beijing_today",
               "to_beijing", "to_beijing_naive")


def _iter_sources():
    for sub in ("app", "tests", "scripts"):
        for p in sorted((BACKEND / sub).rglob("*.py")):
            rel = p.relative_to(BACKEND).as_posix()
            if rel in (AUTHORITY, SELF):
                continue
            yield p, rel


# ---------------------------------------------------------------- 1. 常量唯一性

_OFFSET_RE = re.compile(r"timedelta\(\s*hours\s*=\s*8\s*\)")


def test_no_local_utc8_offset_anywhere():
    """除权威模块外，任何地方都不得自建 UTC+8 偏移。"""
    offenders: list[str] = []
    for path, rel in _iter_sources():
        for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not _OFFSET_RE.search(ln):
                continue
            stripped = ln.strip()
            # 注释与断言不算「自建权威」——断言是在校验权威值本身
            if stripped.startswith("#"):
                continue
            if "==" in stripped or "!=" in stripped:
                continue
            offenders.append(f"{rel}:{i}: {stripped[:90]}")
    assert not offenders, (
        "以下位置自建了 UTC+8 偏移，应改为 `from app.core.bjtime import BJ_TZ / BJ_OFFSET`：\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------- 2. 实现唯一性

_DEF_RE = re.compile(r"^\s*def\s+(\w+)\s*\(")


def test_clock_functions_defined_only_in_authority():
    """时钟函数只能在 `core/bjtime.py` 里定义——不得再出现第二个实现。"""
    offenders: list[str] = []
    for path, rel in _iter_sources():
        for i, ln in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            m = _DEF_RE.match(ln)
            if m and m.group(1) in CLOCK_NAMES:
                offenders.append(f"{rel}:{i}: def {m.group(1)}")
    assert not offenders, (
        "以下位置重复实现了北京时间函数，应改为从 app.core.bjtime 导入：\n  "
        + "\n  ".join(offenders)
    )


# ---------------------------------------------------------------- 3. 防回潮：旧宿主不得重新拥有时钟

def test_trading_status_no_longer_owns_the_clock():
    """`market/trading_status.py` 曾导出 `beijing_now`（36 处消费）——防它回流。"""
    src = (BACKEND / "app/market/trading_status.py").read_text(encoding="utf-8")
    assert "def beijing_now" not in src
    assert "from app.core.bjtime import BJ_TZ" in src, "时区常量必须来自权威模块"


def test_core_db_no_longer_owns_the_clock():
    """`core/db.py` 曾导出 `beijing_now_naive`（20 处消费）——防它回流。"""
    src = (BACKEND / "app/core/db.py").read_text(encoding="utf-8")
    assert "def beijing_now_naive" not in src
    # utcnow 仍在（UTC 语义的表仍用它），但必须带着「别拿它做面向人的时间戳」的警告
    assert "def utcnow" in src
    assert "不要用它做面向人的时间戳" in src, "utcnow 的用途警告被删掉了"


def test_events_extract_no_longer_reimplements_for_cycle_avoidance():
    """`events/extract.py` 曾以「防循环导入」为由独立实现一份北京 naive。

    `core/bjtime.py` 零 app 依赖，循环导入的前提不成立——该借口失效。
    """
    src = (BACKEND / "app/events/extract.py").read_text(encoding="utf-8")
    assert "def _beijing_now_naive" not in src
    assert "from app.core.bjtime import" in src


# ---------------------------------------------------------------- 4. 语义正确性

def test_authority_exposes_both_semantics():
    """aware 与 naive 两种语义都必须存在且**不可互换**。"""
    from app.core import bjtime

    aware = bjtime.beijing_now()
    naive = bjtime.beijing_now_naive()

    assert aware.tzinfo is not None, "beijing_now() 必须是 aware"
    assert naive.tzinfo is None, "beijing_now_naive() 必须是 naive"
    # aware 与 naive 相减必须抛 TypeError —— 这是当年「早了 8 小时」事故的类型级防线
    with pytest.raises(TypeError):
        _ = aware - naive


def test_bj_tz_offset_is_exactly_8_hours():
    from app.core.bjtime import BJ_TZ, BJ_OFFSET

    assert BJ_TZ.utcoffset(None) == timedelta(hours=8)
    assert BJ_OFFSET == timedelta(hours=8)
    # 固定偏移：不随日期变化（中国不实行夏令时）
    assert BJ_TZ.utcoffset(datetime(2026, 1, 1)) == BJ_TZ.utcoffset(datetime(2026, 7, 1))


def test_to_beijing_treats_naive_as_beijing():
    """naive 输入**视为已是北京时间**（2026-09-09 定的口径），不按 UTC 解释。"""
    from app.core.bjtime import BJ_TZ, to_beijing, to_beijing_naive

    naive = datetime(2026, 9, 11, 15, 45, 6)
    assert to_beijing(naive) == naive.replace(tzinfo=BJ_TZ)
    assert to_beijing(naive).hour == 15, "naive 不得被当成 UTC 而 +8 小时"
    assert to_beijing_naive(naive) == naive

    # aware 的 UTC 输入要正确转换
    utc = datetime(2026, 9, 11, 7, 45, 6, tzinfo=timezone.utc)
    assert to_beijing(utc).hour == 15
    assert to_beijing_naive(utc) == naive


def test_beijing_today_matches_beijing_now_date():
    """`beijing_today()` 必须与 `beijing_now().date()` 同源（不得用 date.today()）。"""
    from app.core.bjtime import beijing_now, beijing_today

    assert beijing_today() == beijing_now().date()


# ---------------------------------------------------------------- 5. 双口径 bug 的定点回归

def test_alert_triggered_at_is_not_shifted_again():
    """**定点回归**：`alert.triggered_at` 已是北京 naive，消费方不得再 +8h。

    2026-09-11 实测：库内 `2026-09-11 14:59:31`（北京时间），而
    `feishu.format_alert_text` 仍 `+ timedelta(hours=8)` → 飞书显示 22:59。
    同款缺陷还在 `api/routes/assistant.py`（今日告警过滤，16:00 后恒空）与
    `assistant/tools.py`。
    """
    from app.models.alert import AlertEvent, AlertRule
    from app.notifiers.feishu import format_alert_text

    ev = AlertEvent(id=1, rule_id=1, triggered_at=datetime(2026, 9, 11, 14, 59, 31),
                    symbol="600000", trigger_value=1.0, threshold=0.5, snapshot="{}")
    rule = AlertRule(id=1, name="测试规则", condition_type="price_above")
    text = format_alert_text(ev, rule)

    assert "14:59:31" in text, f"触发时间被二次偏移了：{text!r}"
    assert "22:59" not in text


# ---------------------------------------------------------------- 5. `.today()` 禁令
# （S2-8 阶段 2.5 收口 2026-09-12；扫描面扩至 scripts/tests 同日）

def _find_today_calls(src: str) -> list[int]:
    """返回源码中 `<expr>.today()` 调用的行号（AST 判定）。

    **规则是「任何 `.today()` 都算违例」**，不区分接收者是谁。三版守卫的演进说明为什么：

    - v1 只匹配 `Name(id="date")` ⇒ `from datetime import date as date_cls` + `date_cls.today()`
      整条绕过（`app/services/akshare_ext.py` 就这样漏了 1 处，**而测试当时全绿**）。
    - v2 解了 import 别名，但仍按「接收者是 date 对象」的思路写 ⇒
      `pd.Timestamp.today()`（`scripts/verify_climate_chain.py`）又漏了。
    - v3（本版）索性按**语义**判定：`.today()` 这个属性调用**无论挂在谁身上**，
      取的都是**进程本地时区的今天**——pandas、numpy、第三方封装都一样。
      既然全系统只允许 `beijing_today()` 作为「今天」，那么 `.today()` 就该零容忍。

    ⚠️ **必须走 AST**：文本扫描会被「不要用 date.today()」这类教学文字误伤
    （本文件 docstring 自己就在讲它）。
    """
    import ast

    lines: list[int] = []
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "today"):
            lines.append(node.lineno)
    return sorted(lines)


#: 扫描面。2026-09-12 由 `app/` 扩至三处，理由见下方测试 docstring。
TODAY_SCAN_DIRS = ("app", "scripts", "tests")

#: 权威模块自身豁免（它的实现里允许出现进程取日的历史痕迹；当前已无，留作显式口子）。
TODAY_EXEMPT = (AUTHORITY, SELF)


def test_no_process_timezone_today_anywhere():
    """`app/` `scripts/` `tests/` 内禁止任何 `.today()`——日期归属一律 `beijing_today()`。

    **为什么扩到 scripts/ 与 tests/**：`.today()` 按**进程时区**取日，+8 生产机上
    「碰巧正确」，CI/海外机器错一天。此前只守 `app/`，另两处的 11 个调用点
    （`scripts/` 2 + `tests/` 9）静默存在——而 `tests/` 恰恰最容易出问题：
    夹具用进程时区的「今天」去锚定，被测代码用北京「今天」判定，
    「一直好的测试突然挂」（[[KB-TRADE-02]] 原话）就是这么来的。
    非 CST 机器上行为会变，CST 机器上保持不变。
    """
    offenders: list[str] = []
    for sub in TODAY_SCAN_DIRS:
        for p in sorted((BACKEND / sub).rglob("*.py")):
            rel = p.relative_to(BACKEND).as_posix()
            if rel in TODAY_EXEMPT:
                continue
            for ln in _find_today_calls(p.read_text(encoding="utf-8")):
                offenders.append(f"{rel}:{ln}")
    assert not offenders, (
        "以下位置用了 `.today()`（进程时区取日，跨时区错一天），"
        "应改为 `from app.core.bjtime import beijing_today`：\n  " + "\n  ".join(offenders)
    )


def test_today_detector_sees_every_receiver():
    """**守卫自证**：任何接收者的 `.today()` 都要被识别，否则「已清零」是假结论。

    把注入验证固化成常驻测试——守卫漏检比没有守卫更糟，
    它让口径分裂**看起来已解决**，此后没人再去看。
    前两版都是这样翻车的：v1 漏了 `date_cls.today()`，v2 漏了 `pd.Timestamp.today()`。
    """
    # 四种历史写法（v1/v2 各自漏过的都在内）
    assert _find_today_calls("from datetime import date\nx = date.today()\n") == [2]
    assert _find_today_calls(
        "from datetime import date as date_cls\nx = date_cls.today()\n") == [2]
    assert _find_today_calls("import datetime as dt\nx = dt.date.today()\n") == [2]
    assert _find_today_calls("import datetime\nx = datetime.date.today()\n") == [2]
    assert _find_today_calls("import pandas as pd\nx = pd.Timestamp.today()\n") == [2]
    # 反例：叙述被禁写法的文档文字、权威函数调用、以及**名字里带 today 的其他调用**
    # 都不得被误伤（`beijing_today()` 是函数名整体，不是 `.today` 属性访问）
    assert _find_today_calls('"""不要用 date.today()。"""\nx = beijing_today()\n') == []
    assert _find_today_calls("x = beijing_today()\n") == []
