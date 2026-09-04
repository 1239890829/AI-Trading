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
# 新增调度开关时必须同步在这里补一行（marketdb-sync 是第七个）。
os.environ["ASHARE_REVIEW_SCHEDULER_ENABLED"] = "false"
os.environ["ASHARE_PREMARKET_BRIEF_ENABLED"] = "false"
os.environ["ASHARE_PICKS_WATCHER_ENABLED"] = "false"
os.environ["ASHARE_PICKS_REVIEW_ENABLED"] = "false"
os.environ["ASHARE_THS_SENTINEL_ENABLED"] = "false"
os.environ["ASHARE_SENTIMENT_HISTORY_BACKFILL_ENABLED"] = "false"
os.environ["ASHARE_MARKETDB_SYNC_ENABLED"] = "false"

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
