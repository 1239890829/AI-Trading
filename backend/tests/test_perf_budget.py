"""核心端点延迟预算（S2-12 门禁五：p95 预算）。

**为什么只圈这一小撮端点**：立项要的是"性能回归门禁"，但把所有端点都放进预算会立刻
变成 flaky 源——多数端点依赖外部数据源，CI（无网络）上的耗时由 DNS 失败/超时决定，
跟代码性能无关。**能稳定做预算的只有"不依赖外部数据源的本地只读端点"**：
它们走的是 DB 查询 + 序列化，慢了就是真的慢了。

**阈值怎么来的**（不拍脑袋）：2026-09-11 本地实测（服务在跑）这些端点的均值在
**2~12 ms**，预算统一取 **2000 ms** ≈ 实测的 100 倍以上。这么宽松是因为它要抓的是
**数量级级回归**（某端点从十毫秒突然变成几十秒，往往是一次 N+1 查询或死锁），
不是毫秒级抖动。CI 无网络、DB 冷启动，也远够不着这个数。

**新增端点的纪律**：往 `BUDGETS` 加端点时，先本地实测一遍再定阈值，并在注释里
写明实测值——否则阈值要么形同虚设、要么制造噪音。

与 `tests/test_endpoint_smoke.py` 的分工：那边管「不 500」（正确性），这里管「不超时」（性能）。
"""
from __future__ import annotations

import time

import pytest

#: 路径 → p95 预算（毫秒）。只放不依赖外部数据源的本地只读端点。
BUDGETS: dict[str, int] = {
    "/api/health": 2000,                      # 实测 ~3ms
    "/api/picks/strategy-registry": 2000,     # 实测 ~11ms（含核验产物读取）
    "/api/picks/strategy-health": 2000,       # 实测 ~12ms
    "/api/picks/signal-health": 2000,         # 实测 ~2ms
    "/api/agent/params": 2000,                # 实测 ~5ms
    "/api/agent/params/survival": 2000,       # 实测 ~2ms
    "/api/agent/tasks": 2000,                 # 实测 ~4ms
}

#: 采样次数。取 p95 需要一定样本；5 次是「够算分位数 + 不让 CI 变慢」的折中。
SAMPLES = 5


def _p95(values: list[float]) -> float:
    """样本 p95（与 `app.core.perf._quantile` 同口径：线性插值）。"""
    s = sorted(values)
    if not s:
        return 0.0
    if len(s) == 1:
        return s[0]
    pos = 0.95 * (len(s) - 1)
    lo = int(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


@pytest.mark.parametrize("path,budget_ms", sorted(BUDGETS.items()))
def test_core_endpoint_p95_within_budget(client, path, budget_ms):
    """核心只读端点的 p95 不得超预算。

    注意这里**只断言性能**，不断言状态码——状态码归冒烟测试管。
    端点若因环境报 4xx/5xx，耗时往往更短，不会因为"失败得快"而误判为通过预算。
    """
    samples: list[float] = []
    for _ in range(SAMPLES):
        t0 = time.perf_counter()
        client.get(path)
        samples.append((time.perf_counter() - t0) * 1000)

    p95 = _p95(samples)
    assert p95 < budget_ms, (
        f"{path} p95 = {p95:.1f}ms 超出预算 {budget_ms}ms\n"
        f"全部样本：{[round(x, 1) for x in samples]} ms\n"
        f"若确属业务增长导致，请重新实测后上调预算并在 BUDGETS 注明新实测值；"
        f"若属回归，先修代码——不要靠放宽阈值让门禁变绿。"
    )


def test_budget_endpoints_still_exist():
    """**防静默失效**：白名单里的端点必须仍在 openapi 中。

    端点改名/删除后，`client.get()` 会拿到 404，而 404 照样很快 ⇒
    上面的预算用例会**安静地通过**，门禁就此失效。这里钉死端点必须存在。
    """
    from app.main import app

    spec = app.openapi()
    known = set(spec.get("paths") or {})
    missing = sorted(set(BUDGETS) - known)
    assert not missing, (
        f"这些端点已不在 openapi（改名或删除）：{missing}。"
        f"请同步更新 BUDGETS，否则预算用例会对着 404 空转。"
    )
