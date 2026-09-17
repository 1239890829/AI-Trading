"""指数平滑原语测试（`RSH-003` 切片 2）：合成序列 + 独立 Python 参照，**不依赖真实 marketdb**。

## 为什么参照必须独立于「看数值是否合理」
`EMA_i = Σz / Σv`（`z_j = x_j·(1/c)^rrel_j`、`v_j = (1/c)^rrel_j`）只有在指数取
**「该行距当前行的距离」** 时才成立。若误取「该行距**帧首行**的距离」，权重会全部相等
⇒ **静默退化成等权 SMA**：数值是一个完全正常的移动平均、不报错、不告警。
⇒ 本文件的核心是**陷阱回归**（`test_wrong_form_degenerates_to_equal_weight_sma`）：用错误形态
生成一列，断言它与正确形态**显著不同**、且**逐点等于等权移动平均** —— 证明「拿独立参照逐点
对照」这条判据真的能抓住该缺陷。任何"看数值是否合理"的检查都抓不到它。

## 覆盖
a. 精确性（vs 朴素递归，含"截断误差在 400 根下可忽略"这条文档claim）
b. 陷阱回归（错误形态 = 等权 SMA）· c. 溢出护栏 · d. warmup 语义 · e. 缺失值口径
f. 入参守卫 · g. 多级链式（`source` 指向上一级末级名）
h. **扩展钩子集成**：`ppo20` / `adx14` 经 `novelty.PanelExtension` 接进面板，
   与**独立 Python 参照**（同口径但走"直接加权"路径）逐点比对 —— 覆盖"精确性"到"接线正确"两级。
每类关键断言都带**注入自证**（把缺陷形态注回去，确认判据真变红）。
"""
from __future__ import annotations

import math

import duckdb
import pytest

from tests.duckdb_fixtures import insert_rows

from app.factors import smoothing
from app.factors.smoothing import (
    SMOOTHING_WINDOW_BARS,
    MAX_SAFE_LOG_EXPONENT,
    SmoothSpec,
    alpha_for,
    assert_exponent_safe,
    log_exponent_headroom,
    smooth_ctes,
    warmup_bars,
)

W = SMOOTHING_WINDOW_BARS          # 400：截断窗（根 K 线）
N_BARS = 900                       # 合成序列长度：> 2×W ⇒ 帧全满的行足够多
TOL = 1e-9
CODE_OK = "90.XXSHE000001"         # 完整序列
CODE_GAP = "90.XXSHE000002"        # 含缺失值
GAP_IDX = (500, 517)               # 缺失位置（交易日序号，0 基）
#: 帧完全排满（窗口内**每一行**都有自己的满帧）的最小 `rrel`：`2W − 1`。
FULL_FRAME_MIN_RREL = 2 * W - 1

#: 给 `smooth_ctes` 提供 `rn`（真实路径由 `evaluate._base_cte` 的 `lvl1` 提供）。
_SRC = (
    "src AS (SELECT *, row_number() OVER (PARTITION BY thscode ORDER BY date_ms) AS rn FROM px)",
)


# ---------------------------------------------------------------- 合成序列
def _days(n: int) -> list[int]:
    from datetime import datetime, timedelta, timezone

    out: list[int] = []
    d = datetime(2018, 1, 2, tzinfo=timezone.utc)
    while len(out) < n:
        if d.weekday() < 5:
            out.append(int(d.timestamp() * 1000))
        d += timedelta(days=1)
    return out


def _prices(n: int, seed: float, *, drift: float = 0.0007, vol: float = 0.03,
            start: float = 20.0) -> list[float]:
    """可复现的几何随机游走（**不用 random**：种子写死在参数里，跨机逐位一致）。"""
    x, px, out = seed, start, []
    for _ in range(n):
        x = (x * 1103515245 + 12345) % 2147483648
        px = max(1.0, px * (1.0 + drift + (x / 2147483648 - 0.5) * vol))
        out.append(round(px, 4))
    return out


#: `px` 的列契约（与 base 链 `lvl4` 的重叠部分同名）。
_PX_COLS = ("thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE, close_price DOUBLE,"
            " open_price DOUBLE, high_price DOUBLE, low_price DOUBLE, pc1_raw DOUBLE, tr_f DOUBLE")


def _px_rows() -> list[tuple]:
    """合成 K 线行（`px` 表全量内容）：一行一根 K 线，含 `tr_f`（与 base 链 lvl2 同口径的算好版本）。

    两条序列：`CODE_OK` 完整；`CODE_GAP` 在 `GAP_IDX` 两处 `close_adj` 为 NULL（缺失值口径用）。
    """
    rows: list[tuple] = []
    for code, seed, gaps in ((CODE_OK, 0.17, ()), (CODE_GAP, 0.41, GAP_IDX)):
        close = _prices(N_BARS, seed)
        prev_close: float | None = None
        for i, ts in enumerate(_days(N_BARS)):
            px = close[i]
            o = round(px * (1.0 + ((i * 7) % 11 - 5) * 0.0008), 4)
            hi = round(max(o, px) * 1.012, 4)
            lo = round(min(o, px) * 0.988, 4)
            #: `tr_f` 与 base 链 lvl2 同口径（含"隔夜跳空 >25% 判除权断层 ⇒ NULL"）。
            if prev_close is None or abs(hi - prev_close) > prev_close * 0.25 \
                    or abs(lo - prev_close) > prev_close * 0.25:
                tr = None
            else:
                tr = max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))
            rows.append((code, ts, None if i in gaps else px, px, o, hi, lo, prev_close, tr))
            prev_close = px
    return rows


@pytest.fixture()
def con():
    """内存合成库。

    ⚠️ 用**内存库**而非文件库：本文件不需要"只写不持有连接"的 `db`/`db_file` 双夹具
    （那条约束只在"既要只读打开、又要同进程读写打开**同一个文件**"时才出现）。
    """
    c = duckdb.connect(":memory:")
    c.execute(f"CREATE TABLE px ({_PX_COLS})")
    insert_rows(c, "px", _px_rows())
    yield c
    c.close()


# ---------------------------------------------------------------- 独立参照（两条互不相同的路径）
def _naive_ema(values: list[float], alpha: float) -> list[float]:
    """**朴素递归**（TA-Lib 同形，**不截断**）：`EMA_0 = x_0`；`EMA_i = c·EMA_{i-1} + a·x_i`。

    这是与 SQL **完全不同**的计算路径：没有任何 `pow`、没有窗口求和、没有归一化。
    它单独就能证明「`Σz/Σv` 的成对权重形式 = EMA 递归」这条数学claim。
    """
    c, out, prev = 1.0 - alpha, [], None
    for v in values:
        prev = v if prev is None else c * prev + alpha * v
        out.append(prev)
    return out


def _ref_truncated(values: list[float | None], alpha: float, window: int) -> list[float | None]:
    """**截断窗闭式**参照（与 SQL 同口径，但按"最近 → 最远"的自然顺序直接加权）：

    `Σ_{有效 j∈窗} x_j·c^(i−j) / Σ_{有效 j∈窗} c^(i−j)`；帧未满（`rrel < window`）或当前行缺值 → `None`。
    ⚠️ **与 SQL 的差异只在计算路径**：SQL 用 `(1/c)^rrel` 的比值（为的是让权重变成"行自身的量"
    从而可预计算成列），此处用 `c^(i−j)` 直算 ⇒ 两条路径互为独立实现。
    """
    c = 1.0 - alpha
    powers = [c ** k for k in range(window)]
    out: list[float | None] = [None] * len(values)
    for i in range(len(values)):
        if i + 1 < window or values[i] is None:
            continue
        num = den = 0.0
        for k in range(window):
            v = values[i - k]
            if v is None:
                continue
            num += v * powers[k]
            den += powers[k]
        out[i] = num / den if den else None
    return out


def _values(con, code: str, col: str = "close_adj") -> list[float | None]:
    rows = con.execute(
        f"SELECT {col} FROM px WHERE thscode = ? ORDER BY date_ms", [code]
    ).fetchall()
    return [r[0] for r in rows]


def _run(con, ctes, terminal: str, alias: str):
    sql = ("WITH " + ",\n".join(ctes)
           + f"\nSELECT thscode, date_ms, {alias} AS v FROM {terminal} ORDER BY thscode, date_ms")
    return con.execute(sql).fetchall()


def _smooth(con, specs, *, window_bars: int = W, tag: str = "sm", source: str = "src"):
    """跑一次 `smooth_ctes` 并取回 `(thscode, date_ms, 值)`。"""
    ctes = (*_SRC, *smooth_ctes(tag, specs, source=source, window_bars=window_bars))
    return _run(con, ctes, f"{tag}_s", specs[0].alias)


#: ⚠️ 判定面自证：`smooth_ctes` 的**唯一**正确形态就是"指数 = 距当前行的距离"。
#: 这条结构守卫把"有人把 `min(rn)` 挪进带 ORDER BY 的窗口"挡在静态层面。
def test_prep_offset_is_partition_wide_not_frame_local():
    prep = smooth_ctes("sm", [SmoothSpec("s", "close_adj", 12)], source="src")[0]
    assert "min(rn) OVER (PARTITION BY thscode)" in prep
    assert "ORDER BY" not in prep.split("AS sm_rrel")[0], (
        "`min(rn)` 的窗口里出现了 ORDER BY ⇒ 它变成**帧内最小** ⇒ 权重退化（见陷阱回归）"
    )


# ---------------------------------------------------------------- a. 精确性
@pytest.mark.parametrize("kind,period", [("ema", 12), ("ema", 26), ("wilder", 14)])
def test_window_sql_matches_naive_recursion(con, kind, period):
    """主判据：窗口 SQL 的逐点值 vs **朴素递归**（无 `pow`、无窗口求和）⇒ 机器精度级一致。

    只有在 `rrel ≥ window_bars`（帧排满、已出 warmup）的行上才有定义，故只比那一段；
    同时断言**有效行数**（否则"全部为 NULL"会让 `max()` 空转成假绿）。
    """
    alpha = alpha_for(kind, period)
    ref = _naive_ema(_values(con, CODE_OK), alpha)  # type: ignore[arg-type]
    got = [v for code, _, v in _smooth(con, [SmoothSpec("s", "close_adj", period, kind)])
           if code == CODE_OK]
    assert len(got) == N_BARS
    checked, worst = 0, 0.0
    for i, value in enumerate(got):
        if value is None:
            assert i < W - 1, f"第 {i} 行（已出 warmup）不应为 NULL"
            continue
        checked += 1
        worst = max(worst, abs(value - ref[i]))
    assert checked == N_BARS - (W - 1), checked
    assert worst < TOL, f"{kind}{period} 与朴素递归最大偏差 {worst:.3e} ≥ {TOL}"


@pytest.mark.parametrize("kind,period", [("ema", 12), ("wilder", 14)])
def test_truncation_error_is_negligible_at_400_bars(con, kind, period):
    """文档claim 的机械核验：**截断窗 400 根**引入的误差可忽略（`smoothing.py` 逐项估算）。

    做法 = 两个参照互比：截断窗闭式（同 SQL 口径）vs 朴素递归（无限记忆）。
    若把 `SMOOTHING_WINDOW_BARS` 调小，这条会立刻变红 ⇒ 它是该常量的**下界守卫**。
    """
    values = _values(con, CODE_OK)
    alpha = alpha_for(kind, period)
    trunc = _ref_truncated(values, alpha, W)
    full = _naive_ema(values, alpha)  # type: ignore[arg-type]
    worst = max(abs(t - f) for t, f in zip(trunc, full) if t is not None)
    assert worst < TOL, f"截断窗 {W} 在 {kind}{period} 上误差 {worst:.3e} 不可忽略"


# ---------------------------------------------------------------- b. 陷阱回归（本文件的核心）
def _wrong_form_ctes(tag: str, specs, *, source: str = "src", window_bars: int = W) -> tuple[str, ...]:
    """**缺陷形态**（只在测试里构造，生产代码里不存在）：

    指数取「该行距**帧首行**的距离」`rn − min(rn) OVER (帧)`，而不是「距**当前行的距离**」。
    对满帧，`min(rn)` 恰是帧首行 ⇒ 该值恒为 `W−1`（**常数**）⇒ 窗口内权重全部相等
    ⇒ **静默退化成等权 SMA**。除 `off` / `rrel` 这一处外，其余与 `smooth_ctes` 逐字相同
    （`v` 的置空口径也保持一致）⇒ 差异被隔离成**单一变量**。
    """
    frame = (f"PARTITION BY thscode ORDER BY date_ms "
             f"ROWS BETWEEN {window_bars - 1} PRECEDING AND CURRENT ROW")
    zs, vs, outs = [], [], []
    for s in specs:
        base = 1.0 / (1.0 - alpha_for(s.kind, s.period))
        zs.append(f"CASE WHEN ({s.src}) IS NOT NULL THEN ({s.src}) * pow({base!r}, {tag}_off) END"
                  f" AS z_{s.alias}")
        vs.append(f"CASE WHEN ({s.src}) IS NOT NULL THEN pow({base!r}, {tag}_off) END AS v_{s.alias}")
        outs.append(
            f"CASE WHEN {tag}_rrel >= {window_bars} AND z_{s.alias} IS NOT NULL "
            f"THEN sum(z_{s.alias}) OVER ({frame}) / NULLIF(sum(v_{s.alias}) OVER ({frame}), 0) END"
            f" AS {s.alias}"
        )
    return (
        f"{tag}_prep AS (\n    SELECT *, rn - min(rn) OVER ({frame}) AS {tag}_off,\n"
        f"           rn - min(rn) OVER (PARTITION BY thscode) + 1 AS {tag}_rrel\n    FROM {source}\n)",
        f"{tag}_w AS (\n    SELECT *,\n           " + ",\n           ".join((*zs, *vs))
        + f"\n    FROM {tag}_prep\n)",
        f"{tag}_s AS (\n    SELECT *,\n           " + ",\n           ".join(outs)
        + f"\n    FROM {tag}_w\n)",
    )


def test_wrong_form_degenerates_to_equal_weight_sma(con):
    """陷阱回归：错误形态（距**帧首行**距离）必须与正确形态**显著不同**，且**逐点等于等权 SMA**。

    这条用例是"上面那些精确性断言真的能抓住该缺陷"的证明 —— 若把 `smooth_ctes` 的 `_prep`
    改成错误形态，`test_window_sql_matches_naive_recursion` 必然变红，而本用例指出**为什么**：
    错误形态压根不是 EMA，它就是一个等权移动平均。
    ⚠️ 只在**帧完全排满**的行上比（`rrel ≥ 2W−1`）：帧边界附近的"最老一行"自身帧未满，
    `min(rn) OVER 帧` 不等于帧首行 ⇒ 那时退化不成立（这条边界本身也是口径的一部分）。
    """
    spec = [SmoothSpec("s", "close_adj", 12, "ema")]
    got = [v for code, _, v in _smooth(con, spec) if code == CODE_OK]
    bad = [v for code, _, v in _run(con, (*_SRC, *_wrong_form_ctes("wf", spec)), "wf_s", "s")
           if code == CODE_OK]
    assert bad != got, "注入没有生效：错误形态与正确形态逐位相同"  # 注入自证
    values = _values(con, CODE_OK)
    checked, worst_vs_correct, worst_vs_sma = 0, 0.0, 0.0
    for i in range(FULL_FRAME_MIN_RREL - 1, N_BARS):
        sma = sum(values[i - W + 1: i + 1]) / W          # type: ignore[arg-type]
        assert bad[i] is not None and got[i] is not None
        checked += 1
        worst_vs_correct = max(worst_vs_correct, abs(bad[i] - got[i]))
        worst_vs_sma = max(worst_vs_sma, abs(bad[i] - sma))
    assert checked >= 100, checked
    # ① 错误形态 ≈ 等权 SMA（机制：权重全部相等）
    assert worst_vs_sma < 1e-9, f"错误形态与等权 SMA 偏差 {worst_vs_sma:.3e}（预期为 0 ⇒ 退化）"
    # ② 与正确形态的差异**显著**（量级可断言，且远大于正确形态自身的精度）
    assert worst_vs_correct > 1e-3, f"差异只有 {worst_vs_correct:.3e}，这条判据将抓不住缺陷"
    # ③ 正确形态 vs 朴素递归仍是机器精度（把"变化确实来自权重"闭环）
    ref = _naive_ema(values, alpha_for("ema", 12))  # type: ignore[arg-type]
    worst_correct = max(abs(got[i] - ref[i]) for i in range(FULL_FRAME_MIN_RREL - 1, N_BARS))
    assert worst_correct < TOL


# ---------------------------------------------------------------- c. 溢出护栏
def test_log_exponent_headroom_formula_and_limit():
    """§ 护栏：最坏对数幅值 = `max_bars · ln(1/(1−α))`；超限**报错**，不静默产出 `inf`。"""
    a = alpha_for("ema", 12)
    assert log_exponent_headroom(a, 100) == pytest.approx(100 * math.log(1 / (1 - a)))
    # 本库实测分区最大行数 2435（`smoothing.py` docstring）⇒ 406.8 < 600，安全且有余量
    assert log_exponent_headroom(a, 2435) == pytest.approx(406.8, abs=0.5)
    assert assert_exponent_safe(a, 2435) == pytest.approx(406.8, abs=0.5)
    with pytest.raises(ValueError, match="溢出"):
        assert_exponent_safe(a, 2435 * 2)          # 813.6 > 600
    with pytest.raises(ValueError, match="alpha"):
        log_exponent_headroom(1.0, 100)
    with pytest.raises(ValueError, match="alpha"):
        log_exponent_headroom(0.0, 100)


def test_exponent_guard_injection_self_proof(monkeypatch):
    """注入自证：把**护栏判据**放大 2 倍 ⇒ 超限入参不再报错（护栏被致盲）。"""
    a, max_bars = alpha_for("ema", 12), 2435 * 2
    with pytest.raises(ValueError, match="溢出"):
        assert_exponent_safe(a, max_bars)
    orig = smoothing.log_exponent_headroom
    weakened = lambda alpha, mb: orig(alpha, mb) / 2.0  # noqa: E731
    monkeypatch.setattr(smoothing, "log_exponent_headroom", weakened)
    assert smoothing.log_exponent_headroom is not orig, "注入未生效"
    assert smoothing.log_exponent_headroom(a, max_bars) < MAX_SAFE_LOG_EXPONENT
    assert_exponent_safe(a, max_bars)   # 注入后**不再抛错** ⇒ 上面那条 raises 确实是护栏在挡
    monkeypatch.undo()
    with pytest.raises(ValueError, match="溢出"):
        assert_exponent_safe(a, max_bars)


# ---------------------------------------------------------------- d. warmup 语义
def test_warmup_rows_are_null(con):
    """`rrel < window_bars` 必须为 NULL（不凑值）；`rrel ≥ window_bars` 必须有值。

    三态纪律：截断窗内的"归一化加权均值"**不是** EMA（它没有帧外记忆），输出它等于
    给出"从上市起算的另一种平滑"而不自知 ⇒ 置 NULL。
    """
    w = 40
    got = [(code, v) for code, _, v in _smooth(con, [SmoothSpec("s", "close_adj", 12)], window_bars=w)
           if code == CODE_OK]
    assert len(got) == N_BARS
    for i, (_, v) in enumerate(got):
        if i < w - 1:
            assert v is None, f"第 {i} 行仍在 warmup，却输出了 {v}"
        else:
            assert v is not None, f"第 {i} 行已出 warmup，却为 NULL"
    assert warmup_bars() == SMOOTHING_WINDOW_BARS == 400
    assert warmup_bars(w) == w


def test_warmup_guard_injection_self_proof(con):
    """注入自证：把 warmup 判定从 `>= N` 改成 `>= 1` ⇒ 起首行立刻被凑出值（不再是 NULL）。"""
    spec = [SmoothSpec("s", "close_adj", 12)]
    base = (*_SRC, *smooth_ctes("sm", spec, source="src", window_bars=W))
    mutated = tuple(c.replace(f"sm_rrel >= {W}", "sm_rrel >= 1") for c in base)
    assert mutated != base, "注入未生效：SQL 里没找到 warmup 判定"
    head_base = _run(con, base, "sm_s", "s")[0][2]
    head_mut = _run(con, mutated, "sm_s", "s")[0][2]
    assert head_base is None
    assert head_mut is not None, "把判定放宽后首行仍为 NULL ⇒ 该判据不是 warmup 的判定面"


# ---------------------------------------------------------------- e. 缺失值口径
def test_null_src_nulls_its_own_denominator_term(con):
    """缺失值：某行 `src` 为 NULL ⇒ 该行 `v` **同样置 NULL**（结果 ≠ 只进分母）。

    若只置空分子，该行会**只进分母不进分子** ⇒ 结果被系统性压低（base 链 OBV 的同族注释）。
    本用例把"不置空分母"这个缺陷形态**当场构造出来**，断言两者显著不同且方向一致（偏低）。
    """
    gap = sorted(GAP_IDX)
    values = _values(con, CODE_GAP)
    assert [values[i] for i in gap] == [None, None], "前置：合成序列确实有缺失行"

    spec = [SmoothSpec("s", "close_adj", 12)]
    correct = [v for code, _, v in _smooth(con, spec) if code == CODE_GAP]
    ref = _ref_truncated(values, alpha_for("ema", 12), W)

    # 正确形态 vs 独立参照（同口径）：缺失行之后的窗口内重新归一
    worst = max(abs(c - r) for c, r in zip(correct, ref) if c is not None)
    assert worst < TOL, f"缺失值口径与参照偏差 {worst:.3e}"
    assert correct[gap[0]] is None, "当前行 `src` 为 NULL 时必须不输出（不得外推）"

    # 注入自证：把 `v` 的置空摘掉 ⇒ 结果被系统性压低，且差异显著
    base = 1.0 / (1.0 - alpha_for("ema", 12))
    needle = f"CASE WHEN (close_adj) IS NOT NULL THEN pow({base!r}, sm_rrel) END AS v_s"
    sm = smooth_ctes("sm", spec, source="src")
    assert needle in sm[1], "注入靶点没找到：`_w` 级的 v_s 定义与预期不符"
    mutated = tuple(c.replace(needle, f"pow({base!r}, sm_rrel) AS v_s") for c in sm)
    assert mutated != sm, "注入未生效"
    defect = [v for code, _, v in _run(con, (*_SRC, *mutated), "sm_s", "s") if code == CODE_GAP]
    tail = [i for i in range(gap[1] + 1, N_BARS) if correct[i] is not None]
    worse = max(abs(defect[i] - correct[i]) for i in tail)
    assert worse > 1e-6, f"摘掉 `v` 的置空后结果几乎没变（{worse:.3e}）⇒ 这条判据抓不住该缺陷"
    #: 方向：只在**该缺失仍数值可见**的区间断言——`c^k` 指数遗忘 ⇒ 约 200 根之后
    #: 该行的权重落到 double 精度以下，两个形态在浮点上**完全重合**（下一条断言正是这个）。
    visible = [i for i in range(gap[1] + 1, gap[1] + 120) if correct[i] is not None]
    assert len(visible) > 100
    assert all(defect[i] < correct[i] for i in visible), "缺陷形态在该区间应**系统性偏低**（只进分母）"
    far = [i for i in tail if i >= gap[1] + 300]
    assert max(abs(defect[i] - correct[i]) for i in far) < 1e-9, (
        "指数遗忘应让缺失值的影响在约 200 根后落到 double 精度以下"
    )


def test_gap_row_differs_from_row_that_merely_participates(con):
    """对照：`src` 有值的行不得被当作缺失（防"一律置空"式的过度修正）。"""
    values = _values(con, CODE_GAP)
    ref = _ref_truncated(values, alpha_for("ema", 12), W)
    got = [v for code, _, v in _smooth(con, [SmoothSpec("s", "close_adj", 12)]) if code == CODE_GAP]
    ok_rows = [i for i in range(W - 1, N_BARS) if values[i] is not None]
    assert len(ok_rows) == N_BARS - (W - 1) - len(GAP_IDX)
    for i in ok_rows:
        assert got[i] == pytest.approx(ref[i], abs=TOL)


# ---------------------------------------------------------------- f. 入参守卫
def test_alpha_for_matches_ta_conventions():
    assert alpha_for("ema", 12) == pytest.approx(2 / 13)
    assert alpha_for("ema", 26) == pytest.approx(2 / 27)
    assert alpha_for("wilder", 14) == pytest.approx(1 / 14)
    assert alpha_for("wilder", 1) == pytest.approx(1.0)
    assert alpha_for("ema", 1) == pytest.approx(1.0)


@pytest.mark.parametrize("kind,period,match", [
    ("sma", 20, "未知平滑类型"), ("EMA", 20, "未知平滑类型"), ("", 20, "未知平滑类型"),
    ("ema", 0, "period"), ("wilder", -3, "period"),
])
def test_alpha_for_rejects_bad_input(kind, period, match):
    """**不设默认兜底**：写错必须报错，不能静默按某一种算（否则是静默换口径）。"""
    with pytest.raises(ValueError, match=match):
        alpha_for(kind, period)


def test_alpha_for_guard_injection_self_proof(monkeypatch):
    """注入自证：给 `kind` 加一个默认兜底 ⇒ 上面那些 `raises` 立刻失去判定面。"""
    orig = smoothing.alpha_for
    monkeypatch.setattr(smoothing, "alpha_for", lambda kind, period: 2.0 / (period + 1.0))
    assert smoothing.alpha_for is not orig, "注入未生效"
    assert smoothing.alpha_for("sma", 20) == pytest.approx(2 / 21)   # 静默按 ema 算（缺陷形态）
    monkeypatch.undo()
    with pytest.raises(ValueError, match="未知平滑类型"):
        smoothing.alpha_for("sma", 20)


@pytest.mark.parametrize("specs,match", [
    ((), "不得为空"),
    ((SmoothSpec("s", "close_adj", 12), SmoothSpec("s", "high_price", 26)), "alias 重复"),
])
def test_smooth_ctes_rejects_bad_specs(specs, match):
    with pytest.raises(ValueError, match=match):
        smooth_ctes("sm", list(specs), source="src")


def test_smooth_ctes_rejects_tag_alias_collision():
    """tag 与 alias 冲突会生成同名 CTE / 同名列 ⇒ 必须报错（否则静默互相覆盖）。"""
    with pytest.raises(ValueError, match="冲突"):
        smooth_ctes("sm", [SmoothSpec("sm_s", "close_adj", 12)], source="src")
    with pytest.raises(ValueError, match="冲突"):
        smooth_ctes("sm", [SmoothSpec("sm", "close_adj", 12)], source="src")


@pytest.mark.parametrize("window_bars", [0, 1, -5])
def test_smooth_ctes_rejects_bad_window(window_bars):
    with pytest.raises(ValueError, match="window_bars"):
        smooth_ctes("sm", [SmoothSpec("s", "close_adj", 12)], window_bars=window_bars)


def test_duplicate_alias_guard_is_load_bearing():
    """注入自证：**摘掉 alias 唯一性守卫**（缺陷形态当场构造）⇒ SQL 里出现同名列 ⇒ 静默覆盖。"""
    specs = [SmoothSpec("s", "close_adj", 12), SmoothSpec("s", "high_price", 26)]
    with pytest.raises(ValueError, match="alias 重复"):
        smooth_ctes("sm", specs, source="src")
    #: 缺陷形态 = 不做唯一性检查的同一个构造器（其余逐字相同）
    zs, vs, outs = [], [], []
    for s in specs:
        base = 1.0 / (1.0 - alpha_for(s.kind, s.period))
        zs.append(f"CASE WHEN ({s.src}) IS NOT NULL THEN ({s.src}) * pow({base!r}, sm_rrel) END"
                  f" AS z_{s.alias}")
        vs.append(f"CASE WHEN ({s.src}) IS NOT NULL THEN pow({base!r}, sm_rrel) END AS v_{s.alias}")
        outs.append(f"CASE WHEN sm_rrel >= 400 THEN 0.0 END AS {s.alias}")
    defect = "SELECT *,\n           " + ",\n           ".join((*zs, *vs)) + "\n    FROM sm_prep"
    correct = smooth_ctes("sm", [specs[0]], source="src")[1]
    assert defect != correct, "注入未生效"
    assert "z_s" in defect and defect.count("AS z_s") == 2, (
        "缺陷形态没有产出同名列 ⇒ 该注入无法证明守卫的必要性"
    )


# ---------------------------------------------------------------- g. 多级链式（ADX 形态）
def test_multi_level_chain_sources_previous_stage(con):
    """多级链式：第二级的 `source` = 第一级的末级名，**每次都换 tag**（ADX 的 Wilder 链形态）。

    ## ⚠️ 链式**不会**自动再叠一个 warmup（实测语义，与"有效起点 ≈ 2×窗"的直觉不同）
    第二级只要求**当前行**有值，窗口内**其余行有值的才算样本**（有效样本重新归一）⇒
    它的起点 = `max(本级的 window, 前级首个有值行)`，**不是**两段 warmup 相加。
    代价是链式起点附近的值只建立在**极短**的有效样本上（最极端：仅 1 个样本）。
    ⇒ 真正需要"两段都排满"时，必须由**候选表达式**显式置空（`cnt >= 2×window`）——
    这正是 `smoothing.warmup_bars()` docstring 给研究脚本开的口子（ADX 用例即照此办理）。
    """
    s1_tag, s2_tag, w = "s1", "s2", 40
    spec1 = SmoothSpec("wild", "close_adj", 14, "wilder")
    ctes1 = smooth_ctes(s1_tag, [spec1], source="src", window_bars=w)
    ctes2 = smooth_ctes(s2_tag, [SmoothSpec("chain", "wild", 14, "wilder")],
                        source=f"{s1_tag}_s", window_bars=w)
    rows = _run(con, (*_SRC, *ctes1, *ctes2), f"{s2_tag}_s", "chain")
    got = [v for code, _, v in rows if code == CODE_OK]
    assert len(got) == N_BARS

    values = _values(con, CODE_OK)
    ref1 = _ref_truncated(values, alpha_for("wilder", 14), w)
    ref2 = _ref_truncated(ref1, alpha_for("wilder", 14), w)
    first1 = next(i for i, v in enumerate(ref1) if v is not None)
    first2 = next(i for i, v in enumerate(ref2) if v is not None)
    assert first1 == w - 1, first1                      # 前级：帧排满才有值
    assert first2 == first1, (
        f"第二级的起点 {first2} ≠ 前级起点 {first1} ⇒ 链式两段 warmup 相加的假设不成立"
    )
    worst, checked = 0.0, 0
    for i, v in enumerate(got):
        if ref2[i] is None:
            assert v is None, f"第 {i} 行第二级参照为 NULL，却输出了 {v}"
            continue
        checked += 1
        worst = max(worst, abs(v - ref2[i]))
    assert checked == N_BARS - first2, checked
    assert worst < TOL, f"二级链式与参照最大偏差 {worst:.3e}"

    #: 链式起点附近的**有效样本只有 1 个**（这就是必须靠候选表达式显式置空的原因）
    assert ref2[first2] == pytest.approx(ref1[first2], abs=TOL)


def test_chained_warmup_must_be_enforced_by_candidate_expression(con):
    """注入自证：候选表达式里的 `cnt >= 2×window` 才是 2 段 warmup 的**判定面**。

    构造上一条同形的二级链，把该守卫注回候选表达式 ⇒ 起点从 `w−1` 推到 `2w−1`；
    摘掉 ⇒ 回到 `w−1`。证明"链式自动叠加 warmup"是个**假前提**，必须显式写。
    """
    w = 40
    ctes = (*_SRC, *smooth_ctes("s1", [SmoothSpec("wild", "close_adj", 14, "wilder")],
                                source="src", window_bars=w),
            *smooth_ctes("s2", [SmoothSpec("chain", "wild", 14, "wilder")],
                         source="s1_s", window_bars=w))
    guarded = (f"s2_g AS (SELECT *, CASE WHEN rn >= {2 * w} AND chain IS NOT NULL "
               f"THEN chain END AS g FROM s2_s)",)
    rows = _run(con, (*ctes, *guarded), "s2_g", "g")
    got = [v for code, _, v in rows if code == CODE_OK]
    first = next(i for i, v in enumerate(got) if v is not None)
    assert first == 2 * w - 1, first          # `rn >= 2w` ⇔ 下标 ≥ 2w−1
    assert all(v is None for v in got[: 2 * w - 1])
    assert first > w - 1, "守卫没生效：起点仍停在单段 warmup"


# ------------------------------------------------- h. 扩展钩子集成（合成小仓，接线 + 数学一起验）
N_CODES = 40
LOOKBACK = 90


def _write_market_db(path) -> None:
    """合成市场库（`daily_k` + `daily_k_adj`，`N_CODES` 只 × `N_BARS` 根）：**写完即关连接**。

    ⚠️ 为什么不用同一夹具既写又持有连接：DuckDB 同进程内不允许对已以读写打开的库
    再以**只读**打开（`ConnectionException: Can't open ... with a different configuration`）。
    """
    days = _days(N_BARS)
    c = duckdb.connect(str(path))
    try:
        c.execute(
            "CREATE TABLE daily_k (thscode VARCHAR, date_ms BIGINT, open_price DOUBLE,"
            " high_price DOUBLE, low_price DOUBLE, close_price DOUBLE, volume BIGINT,"
            " turnover DOUBLE)"
        )
        c.execute("CREATE TABLE daily_k_adj (thscode VARCHAR, date_ms BIGINT, close_adj DOUBLE)")
        ks, ads = [], []
        for si in range(N_CODES):
            close = _prices(N_BARS, 0.03 + si * 0.017)
            #: 交叉截面必须**真有序**（否则秩相关恒为 NULL，集成用例会空转成假绿）
            alpha = 0.002 if si % 2 == 0 else -0.002
            for i, ts in enumerate(days):
                px = round(close[i] * (1 + alpha * (i / N_BARS)), 4)
                o = round(px * (1 + ((i * 5 + si) % 9 - 4) * 0.0009), 4)
                hi = round(max(o, px) * 1.011, 4)
                lo = round(min(o, px) * 0.989, 4)
                vol = 1_000_000 + ((i * 37 + si * 11) % 500) * 1_000
                ks.append((f"90.XXSHE{600000 + si}", ts, o, hi, lo, px, vol, vol * px))
                ads.append((f"90.XXSHE{600000 + si}", ts, px))
        insert_rows(c, "daily_k", ks)
        insert_rows(c, "daily_k_adj", ads)
    finally:
        c.close()


@pytest.fixture()
def market_db(tmp_path):
    """合成市场库的**路径**（用例自己建连）——理由见 `_write_market_db`。"""
    path = tmp_path / "mkt.duckdb"
    _write_market_db(path)
    return path


def _lvl2_tr(close_price: float, hi: float, lo: float, prev_close) -> float | None:
    """`tr_f` 的独立实现（与 base 链 lvl2 同口径，含"跳空 >25% 判除权断层 ⇒ NULL"）。"""
    if prev_close is None or abs(hi - prev_close) > prev_close * 0.25 \
            or abs(lo - prev_close) > prev_close * 0.25:
        return None
    return max(hi - lo, abs(hi - prev_close), abs(lo - prev_close))


def _ppo_adx_reference(con, code: str) -> dict[str, list[float | None]]:
    """**独立 Python 参照**：从原始 OHLC 出发，按 Wilder/PPO 口径逐点重算（不碰 SQL）。

    `_ref_truncated` 走"直接加权"路径（`c^(i−j)` 自然顺序），SQL 走 `(1/c)^rrel` 比值路径
    ⇒ 两条路径互为独立实现；此处再叠一层链式（DM → DI → DX → ADX）。
    """
    rows = con.execute(
        "SELECT a.close_adj, b.close_price, b.high_price, b.low_price"
        " FROM daily_k_adj AS a JOIN daily_k AS b USING (thscode, date_ms)"
        " WHERE a.thscode = ? ORDER BY a.date_ms", [code]
    ).fetchall()
    close = [r[0] for r in rows]
    tr, pdm, mdm = [], [], []
    prev_close = prev_hi = prev_lo = None
    for _, cp, hi, lo in rows:
        tr.append(_lvl2_tr(cp, hi, lo, prev_close))
        if prev_hi is None or prev_lo is None or tr[-1] is None:
            pdm.append(None)
            mdm.append(None)
        else:
            up, dn = hi - prev_hi, prev_lo - lo
            pdm.append(up if up > dn and up > 0 else 0.0)
            mdm.append(dn if dn > up and dn > 0 else 0.0)
        prev_close, prev_hi, prev_lo = cp, hi, lo

    aw = alpha_for("wilder", 14)
    atr = _ref_truncated(tr, aw, W)
    pdi_v, mdi_v = _ref_truncated(pdm, aw, W), _ref_truncated(mdm, aw, W)
    pdi = [None if not a else 100.0 * p / a for p, a in zip(pdi_v, atr)]  # type: ignore[operator]
    mdi = [None if not a else 100.0 * m / a for m, a in zip(mdi_v, atr)]  # type: ignore[operator]
    dx = [None if (p is None or m is None or p + m == 0) else 100.0 * abs(p - m) / (p + m)
          for p, m in zip(pdi, mdi)]
    adx = _ref_truncated(dx, aw, W)
    #: 候选表达式里的显式级联守卫（`smoothing.warmup_bars` docstring 给的口子）
    adx = [v if (i + 1) >= 2 * W else None for i, v in enumerate(adx)]

    e12 = _ref_truncated(close, alpha_for("ema", 12), W)
    e26 = _ref_truncated(close, alpha_for("ema", 26), W)
    ppo = [None if (a is None or b is None or b == 0) else 100.0 * (a - b) / b
           for a, b in zip(e12, e26)]
    return {"ppo20": ppo, "adx14": adx}


def _panel_values(con, ext, name: str, code: str) -> list[float | None]:
    """把候选表达式挂在**扩展末级**上取值（真实路径 = `_panel_sql` 的 `scored`）。"""
    from app.factors import novelty
    from scripts import factor_novelty as cli

    cand = cli._BY_NAME[name]
    panel = novelty._panel_sql([cand], 0, 21, ext)
    sql = (f"{panel}\nSELECT {cand.expr} AS v, cnt FROM {ext.source}"
           f" WHERE thscode = ? ORDER BY date_ms")
    return [r[0] for r in con.execute(sql, [code]).fetchall()]


@pytest.mark.parametrize("name", ["ppo20", "adx14"])
def test_extension_candidates_match_independent_reference(market_db, name):
    """**切片 2 的主判据**：`ppo20` / `adx14` 经扩展钩子接进面板后，与独立 Python 参照逐点一致。

    覆盖两级：
    ① **数学**（Wilder 链 / 双 EMA 的口径对不对）——靠 Python 参照（原始 OHLC 起算）；
    ② **接线**（扩展 CTE 是否真的进了面板、末级名/列名是否对上）——靠"从扩展末级取列"这条路径。
    """
    from scripts import factor_novelty as cli

    con = duckdb.connect(str(market_db))
    try:
        ext = cli._extension_for(["ppo20", "adx14"], con)
        assert ext is not None and ext.source == "adx_s2_s"
        assert len(ext.ctes) == 12, len(ext.ctes)
        ref = _ppo_adx_reference(con, "90.XXSHE600000")
        got = _panel_values(con, ext, name, "90.XXSHE600000")
        assert len(got) == N_BARS
        checked, worst = 0, 0.0
        for i, (g, r) in enumerate(zip(got, ref[name])):
            if r is None:
                assert g is None, f"第 {i} 行参照为 NULL（未出 warmup），却输出了 {g}"
                continue
            assert g is not None, f"第 {i} 行参照有值，扩展却给 NULL"
            checked += 1
            worst = max(worst, abs(g - r))
        #: `ppo20` 从 400 根起、`adx14` 从 800 根起（级联守卫）⇒ 有效行数必须分别对上
        期望 = N_BARS - (2 * W - 1) if name == "adx14" else N_BARS - (W - 1)
        assert checked == 期望, f"{name} 有效行数 {checked} ≠ {期望}"
        assert worst < TOL, f"{name} 与独立参照最大偏差 {worst:.3e}"
    finally:
        con.close()


def test_extension_is_load_bearing_and_keeps_baseline_sql(market_db):
    """接线守卫 + 无扩展路径冻结：

    ① 摘掉扩展 ⇒ 候选表达式引用的列不存在 ⇒ **必须当场报错**（证明钩子不是装饰）；
    ② `_extension_for` 对既有 4 候选返回 `None` ⇒ 面板 SQL 与**加钩子前逐字相同**
       （`novelty._PANEL_SQL_BASELINE_SHA256` 的语义在这里被端到端复核）。
    """
    import hashlib

    from app.factors import novelty
    from app.factors.library import FACTORS, FactorDef
    from app.factors.novelty import _PANEL_SQL_BASELINE_SHA256, screen_candidates
    from scripts import factor_novelty as cli

    probes = (FactorDef("alpha_probe", "probe", 21, "close_adj / c20", "x"),
              FactorDef("beta_probe", "probe", 21, "close_adj / c60", "x"))
    sql = novelty._panel_sql(probes, 1700000000000, 21)
    assert len(sql) == 9127, "无扩展路径的 SQL 长度变了（冻结哈希的 spec 见 novelty 常量注释）"
    assert hashlib.sha256(sql.encode()).hexdigest() == _PANEL_SQL_BASELINE_SHA256, (
        "无扩展路径被改动了（冻结哈希不匹配）"
    )

    con = duckdb.connect(str(market_db))
    try:
        assert cli._extension_for(["willr20", "cmo20"], con) is None
        assert cli._extension_for(["ppo20"], con) is not None
        ppo = cli._BY_NAME["ppo20"]
        with pytest.raises(Exception) as err:
            #: 不带扩展跑 ppo20：`ema12` 在 `lvl4` 里不存在 ⇒ 必须报错而不是静默给 NULL
            screen_candidates(con, [ppo], incumbents=list(FACTORS[:3]), lookback_days=30)
        assert "ema12" in str(err.value), str(err.value)

        ext = cli._extension_for(["ppo20", "adx14"], con)
        report = screen_candidates(
            con, [cli._BY_NAME["ppo20"], cli._BY_NAME["adx14"]],
            incumbents=list(FACTORS[:6]), lookback_days=LOOKBACK, horizon=5, extension=ext,
        )
        assert report["meta"]["extension"]["source"] == "adx_s2_s"
        assert report["meta"]["extension"]["n_ctes"] == 12
        assert report["meta"]["extension"]["note"], "口径留痕必须非空"
        for cand in report["candidates"]:
            assert cand["pairs"], f"{cand['name']} 经扩展后没有任何配对 ⇒ 面板空转"
    finally:
        con.close()
