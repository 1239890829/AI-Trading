"""provider 能力注册表反射双向锚定。

注册表是手写静态真值（app/services/provider_capabilities.py），最大的风险是
**漂移**：provider 方法改名/删除后注册表还在声称"有"（幽灵能力）；composite
链上加了新消费方法而注册表不知道（盲区）。两个方向都反射到**类定义本身**，
注册表与代码不同步时测试必红——不依赖任何人记得手工同步。
"""
from __future__ import annotations

import asyncio

from app.data_providers import (
    CompositeProvider,
    EastmoneyProvider,
    SinaProvider,
    ThsFuyaoProvider,
    TencentProvider,
)
from app.services import provider_capabilities as pc

_PROVIDER_CLASSES = {
    "ths": ThsFuyaoProvider,
    "tencent": TencentProvider,
    "eastmoney": EastmoneyProvider,
    "sina": SinaProvider,
}


def test_declared_methods_exist_on_provider_class():
    """方向①：SUPPORTED/STUB ⇒ 方法真实存在于 provider 类（防幽灵能力）。"""
    for src, cls in _PROVIDER_CLASSES.items():
        caps = pc.capabilities_of(src)
        assert caps, f"{src} 未入注册表"
        for method, entry in caps.items():
            if entry["level"] in (pc.SUPPORTED, pc.STUB):
                assert hasattr(cls, method), (
                    f"{src}.{method} 声明为 {entry['level']} 但类上不存在——"
                    "方法改名/删除后注册表未同步"
                )


def test_composite_public_methods_covered_by_every_source():
    """方向②：composite 全部公共消费方法必须在每源条目中显式出现。

    UNSUPPORTED 也要列（"链上不会调它"本身是信息，盲区不是）；
    get_limit_up_ladder 是链外直调，允许是注册表多出的键。
    """
    # composite 自有生命周期/观测面（aclose、breaker_state、provider_health 喂
    # /api/system/providers），不向数据源分发，不属于能力消费面
    composite_own = {"aclose", "breaker_state", "provider_health"}
    public = {
        name
        for name, obj in vars(CompositeProvider).items()
        if callable(obj) and not name.startswith("_") and name not in composite_own
    }
    assert public, "反射拿到空方法面——CompositeProvider 结构变了？"
    assert {"get_quotes", "get_kline", "get_trading_days"} <= public  # 锚定反射有效
    for src, caps in pc.CAPABILITIES.items():
        missing = public - set(caps)
        assert not missing, f"{src} 注册表缺少 composite 消费方法条目: {sorted(missing)}"


def test_known_single_points():
    """单点风险清单抽查：唯一 SUPPORTED 源挂掉即无人兜底的方法。"""
    sp = pc.single_point_methods()
    assert sp.get("get_trading_days") == "ths"
    assert sp.get("get_hot_stock_list") == "ths"
    assert sp.get("get_limit_up_ladder") == "ths"
    assert sp.get("get_minute_line") == "tencent"
    assert sp.get("get_trades") == "eastmoney"
    assert sp.get("get_capital_flow") == "sina"
    assert sp.get("get_board_rankings") == "sina"


def test_stub_not_counted_as_support():
    """STUB 名存实亡：不得进 providers_supporting / single_points。"""
    for src, caps in pc.CAPABILITIES.items():
        for method, entry in caps.items():
            if entry["level"] == pc.STUB:
                assert src not in pc.providers_supporting(method), (
                    f"{src}.{method} 是 STUB 却进了 SUPPORTED 清单"
                )
    # 抽查已知恒空占位
    assert "tencent" not in pc.providers_supporting("get_limit_up_pool")
    assert "ths" not in pc.providers_supporting("get_order_book")
    assert "sina" not in pc.providers_supporting("get_longhu_records")


def test_endpoint_shape():
    """端点直接调路由函数：契约 = {levels, capabilities, single_points}。"""
    from app.api.routes.health import provider_capabilities

    payload = asyncio.run(provider_capabilities())
    assert set(payload) == {"levels", "capabilities", "single_points"}
    assert payload["levels"] == pc.LEVELS
    assert payload["capabilities"] == pc.CAPABILITIES
    assert payload["single_points"] == pc.single_point_methods()
