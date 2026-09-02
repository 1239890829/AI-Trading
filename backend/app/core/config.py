from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASHARE_", env_file=".env", extra="ignore")

    app_name: str = "AShare AI Trader"
    version: str = "0.1.0"
    log_level: str = "INFO"

    # 主源 + 备源（逗号分隔）：ths | tencent | sina | eastmoney | mock；mock 只能单独使用
    data_provider: str = "ths"
    provider_fallbacks: str = "tencent,eastmoney,sina"
    ths_api_key: str = ""  # 同花顺 fuyao 官方 API Key（放 .env，勿提交）
    ths_base_url: str = "https://fuyao.aicubes.cn"
    # 题材官方成分视为有效的时长（linkage-design §3）；过期后懒同步
    theme_members_ttl_hours: int = 168
    # 自选行情轮询周期（秒）：1s = WS 推送节奏的上限（对标专业行情软件秒级体验）。
    # 实时方法（quotes/indices 等）固定腾讯源优先——免费高频源扛 1Hz 轮询，
    # ths 付费配额 + 8s 超时不挡在秒级链路上（composite.REALTIME_METHODS）。
    poll_interval_seconds: float = 1.0
    # 连续刷新失败后，数据年龄超过该秒数才标 stale 并广播——单次瞬时失败
    # （<10s）只算抖动，不闪"数据过期"；红线 2 的"不冒充实时"以 10s 为界。
    stale_after_seconds: float = 10.0
    alert_poll_interval_seconds: float = 5.0
    request_timeout_seconds: float = 5.0

    watchlist: str = "600519,000001,300750,601318"
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    database_url: str = f"sqlite:///{REPO_ROOT / 'data' / 'ashare.db'}"

    # 全市场快照（市场宽度/情绪底座）
    snapshot_poll_interval_seconds: float = 60.0
    snapshot_save_interval_seconds: float = 300.0
    parquet_dir: str = str(REPO_ROOT / "data" / "parquet")

    # ---- 情绪阈值覆盖（P0-3，留空 = 用 engine 默认业界经验值）----
    # JSON 字符串，结构与 app/sentiment/engine.py 的 HEAT_BANDS / EARNING_BANDS 一致：
    # 每指标为 [[上界, 得分, 标签], ...] 升序，末档上界用 null。
    # 非法配置启动即失败（band_config 校验），绝不静默回退默认值。
    sentiment_heat_bands_json: str = ""
    sentiment_earning_bands_json: str = ""
    # 历史分位校准（P0-3b）：用近 N 个交易日的等分分位替代业界绝对经验值。
    # 实测（2026-09-02，120 天样本）绝对阈值下 promo_1to2 的 ≥40% 档 0% 命中、
    # limit_up <25 家档 0% 命中——死档让指标退化成常量，校准后各档占比趋均。
    # 关掉（0）即回到 engine 的业界经验值，界面 bands_source 会显示 defaults。
    # 注意上两条 *_bands_json 优先级更高：显式配置永远压过自动校准。
    sentiment_calibrate: bool = True
    # 历史指标库（data/sentiment_metrics.json）的自动增量维护。
    # 关掉后库停在最后一次回补的日期，校准窗口会随日历漂移 → 界面会显示
    # window_end 供核对，不会静默假装"近 120 个交易日"。
    sentiment_history_backfill_enabled: bool = True
    sentiment_history_lookback: int = 120
    # 增量回补的巡检间隔（秒）：默认 6 小时。回补是增量的（已有日期不重拉），
    # 稳态下每轮只拉 1–2 天 × 2 个请求，配额开销可忽略。
    sentiment_history_backfill_interval_seconds: float = 21600.0

    # ---- 盘前简报与盘中跟踪（选股 2.0，批次 B）----
    # 盘前简报：交易日 08:40 自动生成（方向排序 + 标的池 + 触发/证伪条件），
    # 落盘 data/picks/briefs/YYYYMMDD.json；POST /api/picks/morning-brief/generate 可手动重跑
    premarket_brief_enabled: bool = True
    premarket_brief_hour: int = 8
    premarket_brief_minute: int = 40
    # 盘中跟踪：以当日简报为跟踪清单，交易时段内按 interval 取拍（ths 涨停池 +
    # 东财板块涨幅），确认/证伪判定走 intraday_rules（与回测同一份代码）
    picks_watcher_enabled: bool = True
    picks_watcher_interval_seconds: float = 60.0
    # 环境缓存（phase + promo 分位）刷新间隔：compute_market_sentiment 较重
    # （全市场宽度 + 两天涨停池），60s/拍全量重算太重且浪费数据源配额
    picks_watcher_env_refresh_seconds: float = 600.0
    # 盘后对照（批次 C）：交易日 15:35 对照当日简报方向（四分类+误判分类）
    # 并回填提醒 T+1/T+3 收益；当日已 schedule 复盘过则幂等跳过
    picks_review_enabled: bool = True
    picks_review_hour: int = 15
    picks_review_minute: int = 35

    # ---- ths 涨停原因单点哨兵（P0-B）----
    # 涨停原因/题材标签 100% 依赖 ths（东财 0%，无备源）：交易时段周期探测
    # reason 非空率，低于阈值/连续拉取失败 → AlertEvent 告警（文案含
    # "题材标签可能失效"）；状态随 GET /api/system/providers 可见
    ths_sentinel_enabled: bool = True
    ths_sentinel_interval_seconds: float = 900.0

    # ---- 盘后复盘 Agent ----
    # 分析器：rules（默认，确定性、零成本）| llm（需配 base_url + api_key）
    review_model: str = "rules"
    review_methodology_version: str = "v1"
    # 调度：北京时间几点几分触发收盘复盘
    review_run_hour: int = 15
    review_run_minute: int = 30
    review_scheduler_enabled: bool = True
    review_check_interval_seconds: float = 60.0
    # LLM 分析器（未配置时自动降级到 rules）
    review_llm_base_url: str = ""
    review_llm_api_key: str = ""
    review_llm_model: str = ""

    # ---- 新闻/公告摘要 ----
    # 摘要器：rules（默认，确定性、零成本、永远可用）| llm（需配 base_url + api_key）
    # 规则层已能产出 重要度/情绪/事实摘要/关键数字，LLM 是增强层而非前置依赖
    news_model: str = "rules"
    news_llm_base_url: str = ""
    news_llm_api_key: str = ""
    news_llm_model: str = ""

    # ---- 写接口鉴权（B6，opt-in）----
    # 留空 = 本地开发全放行；部署到公网/NAS 时配置任意随机值，
    # 之后所有写请求必须带 X-API-Token 头（前端 NEXT_PUBLIC_API_TOKEN 自动携带）
    api_token: str = ""

    @property
    def review_dir(self) -> str:
        return str(REPO_ROOT / "data" / "review")

    @property
    def watchlist_symbols(self) -> list[str]:
        return [s.strip() for s in self.watchlist.split(",") if s.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [s.strip() for s in self.cors_origins.split(",") if s.strip()]


settings = Settings()
