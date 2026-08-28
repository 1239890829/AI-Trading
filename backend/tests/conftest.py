from __future__ import annotations

import os

# 必须在导入 app 之前设置：保证测试环境确定性（mock provider + 不主动轮询）
os.environ["ASHARE_DATA_PROVIDER"] = "mock"
os.environ["ASHARE_POLL_INTERVAL_SECONDS"] = "3600"
os.environ["ASHARE_WATCHLIST"] = "600519,000001,300750,601318"
os.environ["ASHARE_DATABASE_URL"] = "sqlite:///:memory:"
