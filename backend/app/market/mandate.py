"""回测 mandate：把"策略/标的/费用/窗口"声明成 yaml 文件，一份 mandate 可复跑。

来源：星标方案 P2（ai-hedge-fund 的 mandate 思想）。此前回测参数散在请求体里，
"上次是怎么跑出这个结果的"无法复现——mandate 把它变成仓库里的声明式文件。

分层规则（显式、可解释）：
    代码默认 < mandate 文件 < 请求显式字段
每次解析返回 `applied`，逐字段说明取值来源——不做黑箱合并。
未知字段**必须报错**而不是静默忽略（项目纪律：不猜）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

from app.market.backtest import STRATEGY_REGISTRY, BacktestConfig

MANDATE_DIR = Path(__file__).resolve().parents[2] / "mandates"

# mandate 里 costs/limits/split 段允许的字段 → BacktestConfig 字段
_COST_KEYS = {
    "initial_cash", "commission_rate", "commission_min",
    "stamp_tax", "slippage_bp", "limit_pct", "limit_eps", "in_ratio",
}

_TOP_KEYS = {"name", "description", "symbol", "strategy", "data", "costs"}


@dataclass
class Mandate:
    """一份已解析的回测声明文件。"""

    name: str
    file: str
    symbol: str
    strategy_id: str
    params: dict = field(default_factory=dict)
    bars: int = 500
    description: str = ""
    config: BacktestConfig = field(default_factory=BacktestConfig)


def list_mandates() -> list[dict]:
    """全部 mandate 概要（供下拉选择）。目录不存在返回空。"""
    if not MANDATE_DIR.exists():
        return []
    out = []
    for p in sorted(MANDATE_DIR.glob("*.yaml")):
        try:
            m = load_mandate(p.stem)
        except Exception as exc:
            out.append({"id": p.stem, "name": p.stem, "file": p.name, "error": str(exc)[:120], "valid": False})
            continue
        out.append({
            "id": p.stem,  # 标识符（load_mandate 的入参）；file 仅用于展示
            "name": m.name,
            "file": m.file,
            "description": m.description,
            "symbol": m.symbol,
            "strategy_id": m.strategy_id,
            "params": m.params,
            "bars": m.bars,
            "valid": True,
        })
    return out


def load_mandate(name: str) -> Mandate:
    """加载并校验一份 mandate。文件不存在/字段非法/策略未知都会显式报错。"""
    name = (name or "").strip()
    # 容错归一：调用方传文件名（含 .yaml）时去掉后缀——列表端点展示 file，
    # 容易误把 file 当标识符传回来；显式归一而不是报错。
    if name.endswith(".yaml"):
        name = name[: -len(".yaml")]
    if not name or "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"非法 mandate 名：{name!r}")
    path = MANDATE_DIR / f"{name}.yaml"
    if not path.exists():
        raise ValueError(f"mandate 不存在：{path.name}（目录 {MANDATE_DIR}）")

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"mandate 必须是 YAML 映射：{path.name}")
    unknown = set(raw) - _TOP_KEYS
    if unknown:
        raise ValueError(f"mandate 含未知字段 {sorted(unknown)}（只允许 {_TOP_KEYS}）")

    symbol = str(raw.get("symbol", "")).strip().zfill(6)
    if not symbol.isdigit() or len(symbol) != 6:
        raise ValueError(f"mandate symbol 非法：{raw.get('symbol')!r}")

    strategy = raw.get("strategy") or {}
    if not isinstance(strategy, dict):
        raise ValueError("strategy 必须是映射 {id, params}")
    unknown_strat = set(strategy) - {"id", "params"}
    if unknown_strat:
        raise ValueError(f"strategy 含未知字段 {sorted(unknown_strat)}")
    strategy_id = str(strategy.get("id", "")).strip()
    if strategy_id not in STRATEGY_REGISTRY:
        raise ValueError(
            f"strategy_id '{strategy_id}' 不在注册表：{sorted(STRATEGY_REGISTRY)}"
        )
    params = strategy.get("params") or {}
    if not isinstance(params, dict):
        raise ValueError("strategy.params 必须是映射")
    if params:
        unknown_params = set(params) - set(STRATEGY_REGISTRY[strategy_id]["params"])
        if unknown_params:
            raise ValueError(
                f"strategy.params 含未知键 {sorted(unknown_params)}，"
                f"合法键 {sorted(STRATEGY_REGISTRY[strategy_id]['params'])}"
            )

    data = raw.get("data") or {}
    if not isinstance(data, dict):
        raise ValueError("data 必须是映射")
    unknown_data = set(data) - {"bars", "source"}
    if unknown_data:
        raise ValueError(f"data 含未知字段 {sorted(unknown_data)}")
    bars = int(data.get("bars", 500))
    if not 100 <= bars <= 500:
        raise ValueError(f"data.bars 须在 100-500：{bars}")

    costs = raw.get("costs") or {}
    if not isinstance(costs, dict):
        raise ValueError("costs 必须是映射")
    unknown_costs = set(costs) - _COST_KEYS
    if unknown_costs:
        raise ValueError(f"costs 含未知字段 {sorted(unknown_costs)}")
    cfg_kwargs = {}
    for k, v in costs.items():
        if not isinstance(v, (int, float)) or isinstance(v, bool):
            raise ValueError(f"costs.{k} 必须是数值：{v!r}")
        cfg_kwargs[k] = float(v)
    if cfg_kwargs.get("initial_cash", 1_000_000) <= 0:
        raise ValueError("costs.initial_cash 必须 > 0")
    config = BacktestConfig(**cfg_kwargs) if cfg_kwargs else BacktestConfig()

    return Mandate(
        name=str(raw.get("name") or path.stem),
        file=path.name,
        symbol=symbol,
        strategy_id=strategy_id,
        params=params,
        bars=bars,
        description=str(raw.get("description") or ""),
        config=config,
    )


@dataclass
class ResolvedBacktest:
    """分层解析结果：代码默认 < mandate < 请求显式字段。"""

    symbol: str
    strategy_id: str
    params: dict
    bars: int
    config: BacktestConfig
    mandate: str | None = None
    applied: list[str] = field(default_factory=list)


def resolve_backtest_request(
    *,
    symbol: str | None,
    strategy_id: str | None,
    params: dict | None,
    bars: int | None,
    mandate_name: str | None,
) -> ResolvedBacktest:
    """按 默认 < mandate < 请求 的顺序解析，applied 记录每个字段的来源。"""
    applied: list[str] = []
    m = load_mandate(mandate_name) if mandate_name else None
    if m:
        applied.append(f"mandate={m.file}")

    def pick(field: str, req_val, man_val, default, fmt=lambda v: v):
        if req_val is not None:
            applied.append(f"{field}←请求")
            return fmt(req_val)
        if man_val is not None:
            applied.append(f"{field}←mandate")
            return fmt(man_val)
        applied.append(f"{field}←默认")
        return default

    sym = pick("symbol", symbol, m.symbol if m else None, "", str).strip()
    # 注意：空串会被 zfill 填成 "000000" 混过 isdigit 校验，必须先判空
    if not sym:
        raise ValueError("symbol 缺失（mandate 与请求均未提供）")
    if not sym.isdigit() or len(sym) > 6:
        raise ValueError(f"非法代码：{symbol!r}")
    sym = sym.zfill(6)

    sid = pick("strategy_id", strategy_id, m.strategy_id if m else None, "")
    if sid not in STRATEGY_REGISTRY:
        raise ValueError(f"strategy_id '{sid}' 不在注册表：{sorted(STRATEGY_REGISTRY)}")

    if params is not None:
        applied.append("params←请求")
        eff_params = params
    elif m and m.params:
        applied.append("params←mandate")
        eff_params = dict(m.params)
    else:
        applied.append("params←注册表默认")
        eff_params = dict(STRATEGY_REGISTRY[sid]["params"])
    unknown_params = set(eff_params) - set(STRATEGY_REGISTRY[sid]["params"])
    if unknown_params:
        raise ValueError(
            f"strategy.params 含未知键 {sorted(unknown_params)}，"
            f"合法键 {sorted(STRATEGY_REGISTRY[sid]['params'])}"
        )

    n_bars = pick("bars", bars, m.bars if m else None, 500, int)
    if not 100 <= n_bars <= 500:
        raise ValueError(f"bars 须在 100-500：{n_bars}")

    config = BacktestConfig()
    if m:
        config = m.config
        applied.append("costs←mandate")

    return ResolvedBacktest(
        symbol=sym, strategy_id=sid, params=eff_params, bars=n_bars,
        config=config, mandate=mandate_name, applied=applied,
    )
