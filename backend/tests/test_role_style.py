"""梯队角色配色表的跨端守卫（S2-10，2026-09-11）。

**病根**：前端曾有两份各自维护的角色配色表（题材看板 9 键、猎场 11 键），
两处都出错且都逃过了 tsc：

1. 题材看板的表缺 `领涨/滞涨/同步` ⇒ `ROLE_STYLE[role]` 取到 `undefined`，
   徽标**静默无色**。`Record<string, string>` 索引签名让 `ROLE_STYLE[role]`
   的类型是 `string` 而非 `string | undefined`，**编译期不可能抓到**。
2. 题材看板还留着一个后端早已不再产出的僵尸键 `情绪票`（全仓仅剩注释与它自己）。

同一角色在两页还是两种颜色（题材看板 `中军=amber/反包=violet`，猎场
`中军=sky/反包=amber`）—— 同一顶帽子两种颜色。

**本测试钉住**：前端唯一配色表 `apps/web/lib/role-style.ts` 的键集必须与后端
权威集 `app.picks.echelon.ROLE_BASE_SCORE` **全等**。后者是角色打分表，覆盖
后端全部产出方（`theme_service.classify_role` 7 角色 + `broken_ladder` 的断板
+ `echelon.classify_non_limit_up_role` 的中军/领涨/滞涨/同步 = 11 键）。

后端新增角色而前端未跟进 → 这里变红，而不是等用户在界面上发现一个无色徽标。
"""

from __future__ import annotations

import re
from pathlib import Path

from app.picks.echelon import ROLE_BASE_SCORE

_TS_PATH = Path(__file__).resolve().parents[2] / "apps/web/lib/role-style.ts"


def _frontend_role_keys() -> list[str]:
    src = _TS_PATH.read_text(encoding="utf-8")
    block = re.search(r"export const ROLE_STYLE[^{]*\{(.*?)\n\};", src, re.S)
    assert block, "未找到前端 ROLE_STYLE（结构变了，同步更新本测试）"
    raw = re.findall(r'^\s*([^\s:]+)\s*:\s*"', block.group(1), re.M)
    return [k.strip().strip('"').strip("'") for k in raw]


def test_frontend_role_style_covers_every_backend_role():
    frontend = set(_frontend_role_keys())
    backend = set(ROLE_BASE_SCORE)

    only_backend = sorted(backend - frontend)
    only_frontend = sorted(frontend - backend)

    assert frontend == backend, (
        f"角色配色表与后端权威集不一致 —— 后端有而前端缺（徽标会静默无色）：{only_backend}；"
        f"前端有而后端无（僵尸键，已无产出方）：{only_frontend}"
    )


def test_role_style_is_a_single_source():
    """配色表只能有一份：两个消费方都必须从 `@/lib/role-style` 取，不得再自建副本。"""
    web = Path(__file__).resolve().parents[2] / "apps/web"
    consumers = [
        web / "components/theme-card.tsx",
        web / "components/picks/pick-card.tsx",
    ]
    for path in consumers:
        src = path.read_text(encoding="utf-8")
        assert "lib/role-style" in src, f"{path.name} 未从共享表导入角色配色"
        assert "ROLE_STYLE: Record<string, string> = {" not in src, (
            f"{path.name} 又自建了一份 ROLE_STYLE —— S2-10 合并的成果正在被撤销"
        )


def test_render_sites_use_role_class_not_raw_index():
    """渲染侧必须走 `roleClass()`：直接索引在缺配时给 `undefined`（无样式徽标）。

    直接索引 `ROLE_STYLE[x]` 在 tsc 眼里永远是 `string`，所以这条只能靠文本守卫。
    """
    web = Path(__file__).resolve().parents[2] / "apps/web"
    for name in ("components/theme-card.tsx", "components/picks/pick-card.tsx"):
        src = (web / name).read_text(encoding="utf-8")
        offenders = re.findall(r"ROLE_STYLE\[[^\]]+\]", src)
        assert not offenders, (
            f"{name} 仍在直接索引配色表：{offenders} —— 改用 roleClass()，"
            f"否则后端新增角色时徽标会静默无色"
        )
