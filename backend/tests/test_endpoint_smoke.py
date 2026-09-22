"""全 GET 端点冒烟（S2-12 门禁一：集成链路）。

**为什么需要这一条**：2026-09-11 实测度量，132 个 GET 端点里有 **75 个（57%）在任何测试里
都从未被触及**——包括当天刚改动的 `/api/agent/params`、`/api/picks/strategy-registry`。
既有测试多为「直接调函数」或「最小 app + 单个路由」，测不到**真实 app 的装配**：
路由装错、依赖注入缺项、响应模型序列化炸，只有把真实 app 跑起来打一遍才会暴露。

本文件用 `conftest.client`（真实 `app.main`，已处理 lifespan）逐个打所有 GET 端点，
断言的是**最低但最要命的一条**：

    **任何端点都不得返回 500。**

⚠️ **只禁 500，不禁 502/503/504**——这是刻意划定的边界（2026-09-11 实测校准）：
| 码 | 含义 | 是否失败 |
|---|---|---|
| **500** | 未捕获异常 = 代码/装配/序列化真炸了 | ❌ **失败** |
| 502 | 数据源失败（如 provider 缺方法、上游挂）| ✅ 合法降级 |
| 503 | 依赖尚未就绪（快照冷启动、库未同步）| ✅ 合法降级 |
| 4xx | 缺参数 / 数据不存在 | ✅ 合法响应 |

第一版断言写成 `< 500`，实测 17 个端点红，逐个看下来**全是合法降级**：
`/api/company/600519` → 502「数据源失败」、`/api/market/breadth` → 503「快照尚未就绪」。
CI 无网络、无运行时状态时这些必然出现，**把它们判成失败等于让门禁永远红着**——
而永远红的门禁会被忽略，比没有更糟。真正的信号只有 500。

⚠️ 与 `api-sweep.js` 的分工（别重复造）：
| | 本文件 | api-sweep.js |
|---|---|---|
| 目标 | 不 5xx（装配/代码层） | 载荷体检（200 但数据空） |
| 后端 | TestClient（离线） | 真实 8000 服务 |
| 场景 | CI 每次提交 | 本地/部署后人工跑 |

**新增端点的纪律**：端点会自动进本测试（从 openapi 取），不用手工登记——
这正是要防的「加了路由但没人测」。若新端点需要特殊参数才能不 5xx，
把参数值补进下面的 `PATH_VALUES` / `QUERY_VALUES`（口径与 api-sweep.js 保持一致）。

⚠️ **副作用声明（2026-09-11 实测）**：本测试会打遍所有 GET 端点，其中部分端点会
惰性写库/刷缓存（如题材目录），**会改变共享内存库的全局状态**。实测影响：
接在本测试之后跑 `test_theme_catalog.py::test_stale_codes_prioritizes_empty` 会失败
——那条断言用了 `max_themes=10` 的截断结果，本测试写入的条目把它关心的题材挤出了前 10。
已把那条断言改为不依赖截断。**后续若再出现「单跑绿、接在冒烟后红」，
先怀疑本测试的全局副作用**（[[KB-ENG-52]]：测试对共享状态的隐式依赖）。
"""
from __future__ import annotations

import pytest

from tests.offline_smoke import isolate_sources


@pytest.fixture(scope="module", autouse=True)
def offline_sources():
    # Module scope also covers the shared TestClient's lifespan startup/shutdown.
    with pytest.MonkeyPatch.context() as monkeypatch:
        # HTTP/requests/TDX 在本模块边界降级；raw socket 由 conftest 的全套测试硬门
        # 统一拥有，避免其它模块的后台动作在本模块 teardown 才被错误归因。
        isolate_sources(monkeypatch, block_raw_sockets=False)
        yield


#: 路径参数替换值（与 `scripts/api-sweep.js` 的 PATH_VALUES 同口径）
PATH_VALUES = {
    "symbol": "600519",
    "code": "600519",
    "trade_date": "20260828",
    "date": "2026-08-28",
    "target_date": "20260828",
    "id": "1",
    "rule_id": "1",
    "change_id": "1",
    "task_id": "1",
    "period": "day",
    "group": "",
    "ts": "2026-08-28",
    "version": "v1",
    "name": "default",
    "strategy_id": "ma_cross",
    "board_code": "BK1024",
    # 补于 2026-09-11：缺它时 `/api/events/{event_id}` 被静默跳过
    # （由 test_path_values_cover_every_path_parameter 抓出）
    "event_id": "1",
    "run_id": "missing-run",
}

#: 必需查询参数的填充值（同样与 api-sweep.js 的 QUERY_VALUES 同口径）
QUERY_VALUES = {
    "symbol": "600519",
    "symbols": "600519,000001",
    "q": "茅台",
    "limit": "5",
    "date": "2026-08-28",
    "trade_date": "20260828",
    "target_date": "20260828",
    "period": "day",
    "bars": "120",
    "periods": "4",
    "min_count": "2",
    "min_boards": "1",
    "days": "10",
    "strategy_id": "ma_cross",
    "rule_id": "1",
    "acknowledged": "false",
    "changeLow": "-10",
    "changeHigh": "10",
    "minAmountYi": "0",
    "minTurnover": "0",
    "excludeST": "true",
    "excludeBJ": "true",
    "excludeNew": "false",
    "top": "20",
}


def _fill_path(path: str) -> str:
    out = path
    for key, val in PATH_VALUES.items():
        out = out.replace("{" + key + "}", val)
    return out


def _required_query(op: dict) -> dict:
    """从 openapi operation 里取**必需**的查询参数并填值。"""
    out: dict[str, str] = {}
    for p in op.get("parameters") or []:
        if p.get("in") != "query" or not p.get("required"):
            continue
        name = p.get("name")
        if name in QUERY_VALUES:
            out[name] = QUERY_VALUES[name]
    return out


def _get_endpoints() -> list[tuple[str, str, dict]]:
    """(路径, 方法, 必需查询参数) —— 只收 GET（本门禁不触碰任何写接口）。"""
    from app.main import app

    spec = app.openapi()
    out = []
    for path, item in (spec.get("paths") or {}).items():
        op = item.get("get")
        if not op:
            continue
        # 仍带未替换的路径参数 ⇒ 说明出现了新的参数名，需要补进 PATH_VALUES
        if "{" in _fill_path(path):
            continue
        out.append((_fill_path(path), "get", _required_query(op)))
    return sorted(out)


@pytest.mark.parametrize("path,method,params", _get_endpoints())
def test_get_endpoint_never_returns_500(client, path, method, params):
    """任何 GET 端点都不得 500。

    500 = 未捕获异常 = 路由装配 / 依赖注入 / 响应序列化真的炸了。
    502/503（数据源失败、依赖未就绪）与 4xx（缺参、无数据）都是合法结果，不算失败——
    它们恰恰是项目红线要求的「降级必须可见」。
    """
    resp = client.get(path, params=params)
    assert resp.status_code != 500, (
        f"{path} 返回 500（未捕获的服务端异常）\n"
        f"请求参数：{params}\n响应前 300 字符：{resp.text[:300]}"
    )


def test_smoke_covers_a_meaningful_number_of_endpoints():
    """**防退化**：冒烟清单不能自己空掉。

    若 openapi 取不到、或路径参数新增后全部被跳过，上面的参数化用例会"安静地什么都不测"。
    这里钉一个下限：至少要有 50 个端点真的进了冒烟（实测 132 个 GET 端点）。
    """
    eps = _get_endpoints()
    assert len(eps) >= 50, f"冒烟清单只有 {len(eps)} 个端点，疑似 openapi 取不到或参数未登记"


def test_path_values_cover_every_path_parameter():
    """**防遗漏**：openapi 里出现的每个路径参数名都必须在 PATH_VALUES 里有值。

    否则该端点会被 `_fill_path` 之后的 `if "{" in ...` 静默跳过——
    巡不到就等于没这道门禁。新增带路径参数的端点时这里会立刻失败。
    """
    from app.main import app

    spec = app.openapi()
    used: set[str] = set()
    for path, item in (spec.get("paths") or {}).items():
        if not item.get("get"):
            continue
        for seg in path.split("/"):
            if seg.startswith("{") and seg.endswith("}"):
                used.add(seg[1:-1])
    missing = sorted(used - set(PATH_VALUES))
    assert not missing, (
        f"这些路径参数没有替换值 ⇒ 对应端点被静默跳过：{missing}。"
        f"请把值补进 PATH_VALUES（口径与 scripts/api-sweep.js 一致）"
    )
