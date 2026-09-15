"""应用启动装配层（`IMP-027`，2026-09-15）。

`app/main.py` 的 `lifespan` 原本约 550 行，把"构建服务"与"声明 25 个常驻循环"
混在一处，既难读也难核。本包把两者分开：

- `services.py` —— `build_services(app)`：按既有顺序构建服务并写 `app.state`；
- `schedulers.py` —— `register_schedulers(reg, app, services)`：只声明，不启动。

**边界（刻意不搬）**：`main.py` 仍是**唯一装配点**，保留 app 对象构造、
`_AUTH_GUARD` 与全部 `include_router`（R22 的设计意图是"哪些路由可以无凭据"
在挂载处一眼可读）、中间件、错误契约与 `validate_auth_posture()`。
表注册（`Base.metadata` 导入副作用）同样留在 `main.py`。
"""
from app.bootstrap.schedulers import COLD_START_SNAPSHOT_DELAY_SECONDS, register_schedulers
from app.bootstrap.services import AppServices, build_services

__all__ = [
    "AppServices",
    "build_services",
    "register_schedulers",
    "COLD_START_SNAPSHOT_DELAY_SECONDS",
]
