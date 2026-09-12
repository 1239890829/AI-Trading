from __future__ import annotations

import os

# 必须在导入 app 之前设置：保证测试环境确定性（mock provider + 不主动轮询）
os.environ["ASHARE_DATA_PROVIDER"] = "mock"
os.environ["ASHARE_POLL_INTERVAL_SECONDS"] = "3600"
os.environ["ASHARE_WATCHLIST"] = "600519,000001,300750,601318"
os.environ["ASHARE_DATABASE_URL"] = "sqlite:///:memory:"

# 调度器全家桶在测试里一律关闭（本机 .env 是生产配置，开关全 on）。
#
# 为什么必须关：lifespan 关闭对 stop-aware 调度器是 stop.set() + await task，
# 而 stop.set() 打不断 in-flight 的 tick await——一旦某 tick 卡在网络调用上
# （测试时间恰在交易时段/盘前窗口时是真实风险），`await task` 永久阻塞，
# TestClient.__exit__ 等 lifespan 完全结束 → 全量 pytest 在首个文件
# （test_alerts，字母序最先）整场挂死。2026-09-03 两连卡死，faulthandler
# 栈转储实证卡点：test_alerts.py::test_alert_channels 的 wait_shutdown。
# 测试不应跑生产调度——要测调度器行为就直接调 loop 函数（各测试已如此）。
#
# **这张表由注册表派生**（S2-2）：唯一真相源是 app/core/scheduler.SCHEDULER_SWITCH_ENV_VARS，
# main.py 的 add(switch=...) 传了未登记的名字会直接 fail-fast（启动期 ValueError）。
# 此前是手写清单 + 注释提醒"新增开关时同步补一行"——2026-09-10 的 event_collector 就是
# 漏补一行的后果：长生命周期 TestClient 下它真的跑起来打网络、重复写 event_direction，
# 撞 UNIQUE 约束后让 test_events / test_theme_catalog 无辜失败。
#
# ⚠️ 这里引入的模块**只依赖标准库**（不得在模块级 import app.core.config）：本文件顶部
# 必须早于 settings 定型，否则下面 ASHARE_REVIEW_MODEL=rules 等设置会被 .env 抢先固化。
from app.core.scheduler import SCHEDULER_SWITCH_ENV_VARS  # noqa: E402

for _switch_env in SCHEDULER_SWITCH_ENV_VARS:
    os.environ[_switch_env] = "false"

# LLM 路由钉回 rules：本机 .env 配了 provider=claude_cli / *_MODEL=llm 时，
# 走 settings 的测试（如 test_news_digest_api）会真的调起 claude 子进程打
# 真实 API（2026-09-04 实测中招）。要测 LLM 链路就直接构造
# LLMAnalyzer/LLMSummarizer 注入桩（test_llm.py 的做法），别让路由层碰网。
os.environ["ASHARE_REVIEW_MODEL"] = "rules"
os.environ["ASHARE_NEWS_MODEL"] = "rules"
os.environ["ASHARE_LLM_PROVIDER"] = "openai"

# 复盘报告的**落盘目录**也必须隔离。
#
# 库走 :memory: 只挡住了"数据行"这一侧；`app.review.storage.REPORT_DIR` 指向的是
# 生产目录 `data/review/reports/`，`save_report` 先写 JSON 再写库，于是测试会：
#   ① 把 `20990101.json` 之类的测试报告留在生产目录里（2026-09-01 实测残留）；
#   ② `tests/test_review.py::_cleanup` 按 trade_date 删文件，一旦传进真实日期
#      就会连真实的 `20260901.json` 一起删掉。
# 这里在 app 被导入后立刻改指向临时目录，测试模块再 `from ... import REPORT_DIR`
# 拿到的就是隔离后的路径。
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

_TMP_REPORT_DIR = Path(tempfile.mkdtemp(prefix="ashare-test-review-reports-"))

import app.review.storage as _review_storage  # noqa: E402

_review_storage.REPORT_DIR = _TMP_REPORT_DIR
_TMP_REPORT_DIR.mkdir(parents=True, exist_ok=True)


# ------------------------------------------- 运行期落盘隔离（第二批，2026-09-12）
#
# 库走 `:memory:`、报告目录已隔离，**仍有两处模块级常量指向真实 `backend/data/`**，
# 且都由端到端测试**间接触发**（写入口自己完全不知情）：
#
# ① `data/leader_archive.json` ← `tests/test_endpoint_smoke.py`。它按 openapi 遍历
#    **132 个 GET 端点**，其中 `/api/events/impact` 与 `/api/picks/leader-archive`
#    会走 `leader_archive.get_archive()` ⇒ TTL 到期即用测试的 **MockProvider**
#    重建并**覆盖真实档案**。污染特征可内容级识别，且**只有两个量是判据**：
#    `symbols_seen=6`（= mock 的 `UNIVERSE[:6]`）与 `days_with_pool=30`
#    （mock 对**任何日历日**都给池；真实只交易日有池，实测 15~22）。
#    ⚠️ `themes={}` **不是**判据——09-11 的真实构建同样是空题材（当日 ths 未返回 reason）。
#    即：判据要看**与数据源实现强绑定的量**，不看"看着像不像有内容"。
# ② `data/cognition_gaps.jsonl` ← `tests/test_assistant.py::test_chat_route_logs_cognition_gap_when_no_tool_used`
#    经 `/api/assistant/chat` → `record_gap()` **追加**一行；每跑一次全量 +1 行
#    （实测该文件 36 行**全部**是同一条测试问题，真实留痕被淹没）。
#
# 判据（实测，非推断）：以运行时刻为界 `find data -type f -newermt <时刻>`，
# 整场 pytest 只捞出这两个文件；其余 20 个"指向 data/ 的模块级常量"（DuckDB 只读、
# 由调度写而测试不触发等）实测未被写入，故**不做无差别重定向**（重定向 `trade_calendar`
# 一类会被读的路径反而会改变测试语义）。
#
# 修法沿用上面 REPORT_DIR 的既定做法：**把入口模块的属性改指沙箱**——两个模块的调用点
# 都是运行时读模块全局，import 后改属性即生效；测试自身的 `monkeypatch.setattr`
# 仍可临时覆盖（test_cognition 已如此，互不干扰）。
# 守卫：`tests/test_data_path_isolation.py`（新增 data 路径常量必须登记 + 注入校验 + 功能回归）。
_DATA_SANDBOX = Path(tempfile.mkdtemp(prefix="ashare-test-data-"))

import app.assistant.cognition as _cognition_mod  # noqa: E402
import app.services.leader_archive as _leader_archive_mod  # noqa: E402

_leader_archive_mod.ARCHIVE_PATH = _DATA_SANDBOX / "leader_archive.json"
_cognition_mod.GAP_LOG_PATH = _DATA_SANDBOX / "cognition_gaps.jsonl"


# ---------------------------------------------------------------- 共享 TestClient
import pytest  # noqa: E402


@pytest.fixture(scope="module")
def client():
    """模块级 TestClient：**一个测试模块只进一次 lifespan**（P1-25，2026-09-10）。

    为什么必须共享：`with TestClient(app)` 每进一次就重建 lifespan（建库 + 起
    QuoteHub / snapshot 服务 / 首次全市场刷新），本机实测单次 **35–46s**；
    `tests/test_api.py` 12 个用例各开一次 = **7 分 24 秒**（实测）。共享后
    该文件降到 **38 秒**。

    ⚠️ **必须是 module 而不是 session**：lifespan 一旦跨模块存活，`app` 就会与
    后续模块自己新建的 TestClient **并存两个 lifespan**（同一个 app 对象）——
    任何没被开关关掉的常驻调度都会在此期间真的跑起来。2026-09-10 实测：session
    作用域下 `event_collector` 反复写入撞 UNIQUE(event_id,target_type,target)，
    拖垮 3 个完全无关的用例（test_events / test_theme_catalog ×2）。
    若将来要把作用域升到 session，**前提是 conftest 顶部已关掉全部常驻调度**。

    ⚠️ 使用时：同一模块内用例**共享进程状态**（内存库 / 自选表 / 模拟盘 / 快照），
    改状态的用例必须自己还原现场（范例：test_api 的 watchlist 删回、paper reset）；
    依赖「全新启动状态」的用例请自建 TestClient。
    """
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as c:
        yield c

