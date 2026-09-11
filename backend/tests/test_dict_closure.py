"""字典闭包守卫（S2-12 门禁三/四：契约 golden 的「字典缺键」+ 闭包测试）。

## 这两道门禁到底在防什么

系统里有一批「**枚举 → 展示文案**」的字典。它们的失效方式非常隐蔽：
枚举侧加了一个值，字典侧**没有对应项** ⇒ 界面不是报错，而是**显示空/显示原始英文 code**，
或者根本不渲染那一档。现有单测测的是"已登记的那些值对不对"，测不出"**有没有漏登记**"。

这就是「闭包」的含义：**字典的键集必须与枚举全集相等**（不多不少）。
多一个键 = 有僵尸项（删了枚举没删文案）；少一个键 = 有缺口（加了枚举没加文案）。

立项把「契约 golden」与「闭包测试」列为两道，实测下来它们是同一件事的两面：
golden 锁**内容**（值对不对），闭包锁**覆盖**（键全不全）。本文件合起来做。

## 为什么不用"每个字典手写一条断言"

手写会漏——**新增字典时没人想起来加断言**（这正是要防的失效方式）。
所以这里提供工具函数 `assert_covers()`，并在最后加一条**元守卫**：
`test_closure_tool_is_actually_used`，钉住本文件至少覆盖了若干组映射，
防止有人把下面的用例删空后测试依然"绿着"。
"""
from __future__ import annotations

from typing import Iterable, Mapping, get_args


def assert_covers(
    mapping: Mapping,
    universe: Iterable,
    *,
    name: str,
    allow_extra: bool = False,
) -> None:
    """断言 `mapping` 的键集与 `universe` **完全相等**（闭包）。

    :param name: 用于失败信息的名字（写明是哪个字典）
    :param allow_extra: 是否容忍"字典里有枚举外多余键"。默认**不容忍**——
        多余键通常是删了枚举却忘了删文案的僵尸项，留着会让人以为那档还在产出。
    """
    keys = set(mapping)
    want = set(universe)
    missing = sorted(want - keys)
    assert not missing, (
        f"{name}：枚举里有、字典里没有 ⇒ 这些档会显示空或原始 code：{missing}"
    )
    if not allow_extra:
        extra = sorted(keys - want)
        assert not extra, (
            f"{name}：字典里有、枚举里没有 ⇒ 僵尸项（枚举已删但文案还留着）：{extra}"
        )


# ---------------------------------------------------------------- 量价六态


def test_volume_state_label_covers_every_state():
    """`_LABEL` 必须覆盖 `VolumeState` 全集（7 态）。

    值是 `Literal`，用 `get_args` 拿全集 ⇒ **真闭包**，不是照着字典抄一份键来比对
    （那样字典自己加错键也测不出来）。
    """
    from app.market.volume_state import VolumeState, _LABEL

    assert_covers(_LABEL, get_args(VolumeState), name="volume_state._LABEL")


def test_volume_context_note_covers_state_times_context():
    """`_CONTEXT_NOTE` 是**二维**字典（状态 × 语境），比 `_LABEL` 更容易漏。

    只检查第一维会放过"某个状态只写了两种语境"的情况——界面上就表现为
    该状态在板块/大盘页没有解读文案。
    """
    from app.market.volume_state import Context, VolumeState, _CONTEXT_NOTE

    states, contexts = get_args(VolumeState), get_args(Context)
    assert_covers(_CONTEXT_NOTE, states, name="volume_state._CONTEXT_NOTE(状态维)")
    for state, by_ctx in _CONTEXT_NOTE.items():
        assert_covers(by_ctx, contexts, name=f"_CONTEXT_NOTE[{state!r}](语境维)")


# ---------------------------------------------------------------- 情绪档位


def test_level_labels_match_the_cut_definitions():
    """档位标签的数量必须等于档位定义的数量。

    `HEAT_LEVEL_CUTS` / `EARNING_LEVEL_CUTS` 决定"有几个档"，
    `LEVEL_LABELS` 给每档一个名字。两者一旦错位，界面会把"高热"贴到"中性"上
    ——不报错、不崩溃，只是**静默地给出错误的定性**。
    """
    from app.sentiment.engine import EARNING_LEVEL_CUTS, HEAT_LEVEL_CUTS, LEVEL_LABELS

    for dim, cuts in (("heat", HEAT_LEVEL_CUTS), ("earning", EARNING_LEVEL_CUTS)):
        n_levels = len(cuts)
        labels = LEVEL_LABELS[dim]
        assert len(labels) == n_levels, (
            f"{dim} 档位数 = {n_levels}（由 *_LEVEL_CUTS 定义），"
            f"但 LEVEL_LABELS[{dim}] 只有 {len(labels)} 个标签 ⇒ 定性会错位"
        )


def test_phase_matrix_dimensions_match_level_counts():
    """相位矩阵是 `[earning][heat]` 二维表，维度必须等于两边的档位数。

    矩阵是**手写**的常量表，档位数改了它不会自动变 ⇒ 少一行/少一列时
    越界会落到别的格子上，给出错误的周期判定（这是交易信号级错误）。
    """
    from app.sentiment.engine import EARNING_LEVEL_CUTS, HEAT_LEVEL_CUTS, _PHASE_MATRIX

    n_heat, n_earning = len(HEAT_LEVEL_CUTS), len(EARNING_LEVEL_CUTS)
    assert len(_PHASE_MATRIX) == n_earning, (
        f"相位矩阵行数 {len(_PHASE_MATRIX)} ≠ earning 档位数 {n_earning}"
    )
    for i, row in enumerate(_PHASE_MATRIX):
        assert len(row) == n_heat, (
            f"相位矩阵第 {i} 行列数 {len(row)} ≠ heat 档位数 {n_heat}"
        )


# ---------------------------------------------------------------- 策略状态 ↔ 核验结论


def test_verification_expectation_covers_every_strategy_status():
    """**定点回归（S2-11 引入的映射）**：登记册状态 → 预期核验结论的映射必须封闭。

    进化大脑的 `strategy_verification` 用这张表判断「状态与实测结论是否打架」。
    若将来新增一种状态（如 `deprecated`）而没登记进映射，该状态会**静默地不参与
    冲突检测**——看起来一切正常，实则漏检。
    """
    from app.picks import strategy_registry as sr
    from app.services import evolution as evo

    # 从模块常量取全集，而不是手写列表（手写会漏）
    statuses = {v for k, v in vars(sr).items() if k.startswith("STATUS_") and isinstance(v, str)}
    assert statuses, "未找到任何 STATUS_* 常量（改名了？同步更新本测试）"

    src_expected = _read_expected_map(evo, sr)
    assert_covers(src_expected, statuses, name="evolution._collect_strategy_verification 的 expected 映射")


def _read_expected_map(evo, sr) -> dict:
    """从 `_collect_strategy_verification` 的源码里读出 expected 映射。

    它定义在函数体内（局部字典），拿不到运行时对象，只能解析源码——
    若日后改写成模块级常量，本函数可以直接返回它而不必再解析。

    ⚠️ 键是 `STATUS_*` 这类**常量名**，而它们是**函数内 import** 的
    ⇒ 不在 `evolution` 的模块命名空间里，必须从 `strategy_registry` 取值。
    （第一版写 `getattr(evo, ...)` 拿到的是常量名本身，比对自然全不匹配。）
    """
    import ast
    import inspect
    import textwrap

    src = textwrap.dedent(inspect.getsource(evo._collect_strategy_verification))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = {t.id for t in node.targets if isinstance(t, ast.Name)}
            if "expected" not in targets:
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            out = {}
            for k, v in zip(node.value.keys, node.value.values):
                if isinstance(k, ast.Name) and isinstance(v, ast.Constant):
                    out[getattr(sr, k.id, k.id)] = v.value
            if out:
                return out
    raise AssertionError("在 _collect_strategy_verification 里找不到 expected 映射（结构变了？）")


# ---------------------------------------------------------------- 元守卫


def test_closure_tool_is_actually_used():
    """**防空转**：本文件必须真的覆盖了若干组映射。

    若有人把上面的用例删空、或工具函数没被调用，测试依然"全绿"——
    字典缺键却没人发现。这里钉住最小覆盖组数。
    """
    import inspect

    here = inspect.getsource(inspect.getmodule(assert_covers))
    used = here.count("assert_covers(") - 1  # 减去函数定义自身
    assert used >= 4, f"assert_covers 只被调用了 {used} 次，闭包守卫疑似被架空"
