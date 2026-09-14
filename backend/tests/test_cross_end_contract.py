"""跨端字典契约（S2-12 余项：前端硬编码枚举 ↔ 后端枚举源）。

## 为什么需要统一机制

前端有一批「后端枚举 → 中文文案」的字典（`Record<string, string>`）。它们的失效方式是：
后端枚举加了一个值、前端没跟 ⇒ 界面**不是报错**，而是 `map[q] ?? q` 把原始英文 code
直接显示出来（用户看到 `invalid`、`spike` 这类词）。

此前只有两例有守卫（S2-9 三态标签、S2-10 角色配色），其余**靠人记得加**。
本文件把它们统一成一张**契约清单**：新增一对只需加一条记录，不必再写一个测试文件。

## 两道防线

1. **覆盖性**：前端必须存在某个字典，其键集**覆盖**后端枚举全集。
2. **不遗漏**：扫描前端所有中文字典候选，凡未登记进契约、也不在豁免清单里的
   ⇒ 测试失败，逼你去登记或显式豁免。**新增前端字典时这里会立刻红**，
   这正是要防的「加了字典没人管」。

## 口径说明

- 只要求**覆盖**（前端键 ⊇ 后端全集），不要求相等——前端可能有额外的展示项
  （如 `null: "—"` 这类兜底键），那不是错误。
- 定位方式：在指定文件里找「包含后端枚举中至少 2 个值」的那个字典，
  而不是按常量名定位（函数内的 `const map` 同名很常见，按名字取会取错）。
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WEB_LIB = REPO / "apps" / "web" / "lib"

#: 扫描前端 `const X: Record<string, string> = { ... }` 形式的中文文案字典。
#: ⚠️ 这里只匹配**前缀**，块体由 `_scan_dicts` 用花括号配对取出——
#: 第一版把块体也写成正则 `\{(.*?)\n\s*\}`，对**单行**字典完全失效：
#: 文件末尾的单行字典因没有换行而漏扫（防遗漏机制形同虚设），
#: 而中间的单行字典会一路吞到下一个 `}`（实测给 TRI_LABELS 混进一个噪音键 `v`）。
_DICT_RE = re.compile(
    r"const\s+(\w+)\s*:\s*Record<\s*string\s*,\s*string\s*>\s*=\s*\{",
)
#: 块内键名。三种写法都要认：`high: "正常"` / `"null": "—"` / **中文键**
#: （角色配色表就是 `空间板: "border-...`，第一版只认 ASCII 键 ⇒ 扫出空集、
#: 角色契约的匹配全部落空——中文键在本项目里是常态，不是例外）
_KEY_RE = re.compile(r"[\"']?([\w一-鿿/]+)[\"']?\s*:")


def _scan_dicts(rel_path: str) -> list[tuple[str, set[str]]]:
    """扫描一个前端文件里的所有中文字典候选，返回 [(常量名, 键集)]。

    块体用**花括号配对**取出而非正则，这样单行、多行、文件末尾的写法都能正确处理。
    """
    src = (WEB_LIB / rel_path).read_text(encoding="utf-8")
    out = []
    for m in _DICT_RE.finditer(src):
        name = m.group(1)
        start = m.end() - 1  # 指向 '{'
        depth = 0
        for j in range(start, len(src)):
            if src[j] == "{":
                depth += 1
            elif src[j] == "}":
                depth -= 1
                if depth == 0:
                    # 先剥掉所有双引号内容（**值**都在引号里），再提取键——
                    # 否则 CSS 类名里的 `dark:` 会被当成键（角色配色表的值全是
                    # `border-rose-500/50 ... dark:text-rose-300`，实测混进 `dark`）。
                    body = re.sub(r"\"[^\"]*\"", "", src[start + 1:j])
                    out.append((name, set(_KEY_RE.findall(body))))
                    break
    return out


def _find_dict_covering(rel_path: str, must_have: set[str]) -> set[str] | None:
    """在该文件里找一个**包含** `must_have` 全部键的字典，返回其键集；找不到返回 None。"""
    for _name, keys in _scan_dicts(rel_path):
        if must_have <= keys:
            return keys
    return None


#: ---------------------------------------------------------------- 契约清单
#:
#: 新增跨端字典时**在这里加一条**即可，不必另写测试。
#: `universe` 是后端枚举全集（用 callable 延迟求值，避免测试收集期就 import 业务模块）。
CONTRACTS: list[dict] = [
    {
        "name": "行情质量等级",
        "file": "format.ts",
        "universe": lambda: {q.value for q in _quality_enum()},
        "note": "缺键时 qualityLabel 会把原始 code（如 invalid）直接显示给用户",
    },
    {
        "name": "三态标签",
        "file": "format.ts",
        "universe": lambda: set(_tri_labels()),
        "note": "S2-9：后端有 null→—、前端漏键会直接打出 null",
    },
    {
        "name": "梯队角色配色",
        "file": "role-style.ts",
        "universe": lambda: set(_role_base_score()),
        "note": "S2-10：后端新增角色而前端无配色 ⇒ 徽标无色",
    },
    {
        "name": "新鲜度状态→陈旧标记",
        "file": "format.ts",
        "universe": lambda: set(_freshness_states()),
        "note": "IMP-002：后端新增状态而无标记 ⇒ 界面与「实时」渲染同形（红线 2 界面层缺口）",
    },
]

#: 扫描到但**不需要**契约的字典（纯前端概念，无后端枚举源）
#: ⚠️ 每一项都要写明理由——豁免不是"懒得登记"的借口。
#: `test_exempt_entries_are_still_real` 会校验它们真实存在，防止拼错的豁免
#: （那等于永久放行一个真字典）。
EXEMPT = {
    # 数据源名：后端 provider 标识散落各处（四源链写在配置与各 provider 内），
    # 无集中枚举源可做闭包，故只由前端维护。
    ("format.ts", "SOURCE_LABELS"),
    # 路由来源标签：键是**前端路由路径**（/hunting、/agent…），不是后端枚举。
    ("routing.ts", "labels"),
}


def _quality_enum():
    from app.schemas.market import Quality

    return Quality


def _tri_labels():
    from app.picks.push_cards import _TRI_LABELS

    return _TRI_LABELS


def _freshness_states():
    from app.core.freshness import STATES

    return STATES


def _role_base_score():
    from app.picks.echelon import ROLE_BASE_SCORE

    return ROLE_BASE_SCORE


# ---------------------------------------------------------------- 覆盖性


def _all_contracts():
    """展开为 [(name, file, universe)]，universe 求值失败时跳过并报错说明。"""
    out = []
    for c in CONTRACTS:
        out.append((c["name"], c["file"], c["universe"]()))
    return out


def test_contracts_are_non_empty():
    """契约清单自己不能是空的——否则下面所有断言都会"安静地什么都不测"。"""
    assert CONTRACTS, "契约清单为空"
    for c in CONTRACTS:
        universe = c["universe"]()
        assert universe, f"契约「{c['name']}」的后端枚举全集为空（取错了？）"


def test_frontend_dicts_cover_backend_enums():
    """每个契约：前端必须存在覆盖后端枚举全集的字典。"""
    problems = []
    for name, rel, universe in _all_contracts():
        found = _find_dict_covering(rel, universe)
        if found is None:
            problems.append(
                f"「{name}」：{rel} 里没有覆盖后端全集 {sorted(universe)} 的字典"
            )
            continue
        missing = sorted(universe - found)
        if missing:
            problems.append(f"「{name}」：前端缺键 {missing} ⇒ 会直接显示原始 code")
    assert not problems, "跨端字典不一致：\n  " + "\n  ".join(problems)


# ---------------------------------------------------------------- 不遗漏


def test_no_unregistered_chinese_dicts():
    """**防遗漏**：前端出现的中文字典必须已登记契约或显式豁免。

    新增一个 `Record<string, string>` 而没人管时，这里会红——逼你做出选择：
    登记契约（有后端枚举源）或加进 EXEMPT（纯前端概念并写明理由）。
    """
    registered_files = {c["file"] for c in CONTRACTS}
    unregistered = []
    for ts in sorted(WEB_LIB.glob("*.ts")):
        for name, _keys in _scan_dicts(ts.name):
            # ⚠️ 豁免必须**先于**"文件是否登记"判断：豁免项所在的文件往往
            # 根本不在契约清单里（如 routing.ts），先判文件会直接把它当成未登记。
            if (ts.name, name) in EXEMPT:
                continue
            if ts.name not in registered_files:
                unregistered.append(f"{ts.name}:{name}")
                continue
            # 文件已登记，但具体这个常量是否就是契约目标？
            # 只要它的键集被某个契约的全集覆盖（或反之），视为已登记
            if not any(u <= _keys or _keys <= u
                       for _n, _f, u in _all_contracts() if _f == ts.name):
                unregistered.append(f"{ts.name}:{name}")
    assert not unregistered, (
        f"这些前端中文字典未登记跨端契约、也不在豁免清单里：{unregistered}。"
        f"请在 CONTRACTS 登记（有后端枚举源）或加进 EXEMPT（纯前端概念，写明理由）"
    )


def test_exempt_entries_are_still_real():
    """豁免清单里的项必须**真实存在**——否则拼错的豁免等于永久放行一个真字典。"""
    for rel, name in EXEMPT:
        names = {n for n, _ in _scan_dicts(rel)}
        assert name in names, (
            f"豁免项 {rel}:{name} 在该文件里找不到 ⇒ 要么已改名（同步更新 EXEMPT），"
            f"要么当初写错了（那等于放行了真字典）"
        )
