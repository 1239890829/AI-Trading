from __future__ import annotations

import os

# 必须在导入 app 之前设置：保证测试环境确定性（mock provider + 不主动轮询）
os.environ["ASHARE_DATA_PROVIDER"] = "mock"
os.environ["ASHARE_POLL_INTERVAL_SECONDS"] = "3600"
os.environ["ASHARE_WATCHLIST"] = "600519,000001,300750,601318"
os.environ["ASHARE_DATABASE_URL"] = "sqlite:///:memory:"
# 生产默认开启受限自治；测试显式停止影子评估/实验裁决。
# 此开关不停止建议议程；共享 client 在下方单独隔离该后台任务。
# 自治和议程调度的专门测试仍直接调用真实循环。
os.environ["ASHARE_AGENT_AUTONOMY_ENABLED"] = "false"

# 带配置开关的调度器在测试里关闭；无开关任务不在这份清单内。
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
# TypeSafe/Jev 也是外部网络增强层：本机 Keychain 会把 TYPESAFE_API_KEY 注入新 shell，
# 因此测试必须显式关掉；专项测试通过 monkeypatch + 假 _post 验证，不得碰真实 API。
os.environ["ASHARE_JEV_ENABLED"] = "false"
os.environ["ASHARE_JEV_USAGE_LOG_ENABLED"] = "false"
os.environ["ASHARE_JEV_ALERT_TRIAGE_MODE"] = "off"
os.environ["ASHARE_JEV_EVENT_AUX_MODE"] = "off"
os.environ["ASHARE_JEV_ASSISTANT_TOOL_MODE"] = "off"
os.environ["ASHARE_JEV_ASSISTANT_VERIFY_MODE"] = "off"

# 逐笔的 **TDX 直连降级备源**必须关掉（`IMP-038`，2026-09-16）。
#
# 为什么单靠 `ASHARE_DATA_PROVIDER=mock` 不够：TDX 走 **TCP 7709 真实网络**，
# 不是 HTTP provider，**mock 替不掉它**。`app.market.tdx_tick` 的降级链是
# 「链 → TDX」，链**命中**时确实不会碰 TDX，但**链返回空或抛异常时**会
# ⇒ `tests/test_depth_tools.py` 的 `_Prov(trades=[])` 桩、以及任何链上失败的用例
# 都会真的去连 TDX 服务器（2026-09-16 全量实测：两条用例静默拿到 200 行真实逐笔，
# 断言以"找不到 10.5"的形式变红，而真因是**测试触网**）。单测不得有网才过、
# 不得随行情变 ⇒ 与调度器全家桶、`ASHARE_DATA_PROVIDER=mock` 同一纪律。
# 要测降级链本身，直接注入假 `fetch_tdx_trades`（见 `tests/test_tdx_tick.py`）。
os.environ["ASHARE_TRADES_TDX_FALLBACK_ENABLED"] = "false"

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


# ------------------------------------------- 运行期落盘隔离（第三批，2026-09-14）
#
# ⚠️ 前两批用的是**人工清点**（"还有哪些常量指向 data/"），于是漏掉了靠
# `REPO_ROOT` 间接构造的那一类：`_DECLARED` 的扫描判据要求赋值表达式里同时出现
# `__file__` 与 `'data'`，而 `REPORT_DIR = REPO_ROOT / "data" / ...` 里**没有**
# `__file__` ⇒ 整族常量对守卫不可见（`tests/test_data_path_isolation.py`
# 的扫描面已同步补上传递闭包，见该文件）。
#
# 本次改用**进程内审计钩子实测**（不依赖"看着像不像"，也不被常驻 8000 的写入污染）：
# `sys.addaudithook` 记录本进程对仓库 data/ 的全部写操作，整场 2790 项 pytest
# **只写 7 个真实文件**——`data/review/predictions/20991201..20991207.json`
# （`tests/test_predict.py` 用 `save_report` 直写，测试自带 2099 年假日期）。
# 其余 REPO_ROOT 派生的目录（`data/picks/briefs`、`data/picks/heat`、
# `data/review/methodology`）实测零写入 ⇒ 不重定向（无差别重定向会改变读语义）。
#
# 危害同第二批：测试残留 2099 年假报告在**生产目录**里，而该目录的
# `20260831.json` 是真实预判——一旦有测试用真实日期，就会静默覆盖真数据。
import app.predict.storage as _predict_storage  # noqa: E402

_predict_storage.REPORT_DIR = _DATA_SANDBOX / "predictions"

# BUG-021: the full suite indirectly saves today's empty position plan too.
# Per-test monkeypatches do not cover those callers; isolate the shared default.
import app.picks.position_engine as _position_engine  # noqa: E402

_position_engine._PLAN_DIR = _DATA_SANDBOX / "position_plans"


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
    from app.services import evolution

    async def idle_agenda(app, stop, **kwargs):
        await stop.wait()

    # BUG-012 / IMP-043: autonomy=False still generates advisory agendas after
    # 15:45. Those unrelated background reads can roll back another session on
    # the shared StaticPool connection. Keep the task lifecycle, isolate its work.
    # test_evolution drives the real scheduler directly without this fixture.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(evolution, "evolution_scheduler", idle_agenda)
        with TestClient(app) as c:
            yield c
