from __future__ import annotations

import os

# 必须在导入 app 之前设置：保证测试环境确定性（mock provider + 不主动轮询）
os.environ["ASHARE_DATA_PROVIDER"] = "mock"
os.environ["ASHARE_POLL_INTERVAL_SECONDS"] = "3600"
os.environ["ASHARE_WATCHLIST"] = "600519,000001,300750,601318"
os.environ["ASHARE_DATABASE_URL"] = "sqlite:///:memory:"

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
