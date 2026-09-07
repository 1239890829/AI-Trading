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
    # window_end 供核对，不会静默假装"近 N 个交易日"。
    # lookback=250 对齐方法论"滚动分位近 250 日"；ths 日历只回溯一年（~243
    # 个交易日），backfill 按日历深度自动截短，250 是留有余量的目标值。
    sentiment_history_backfill_enabled: bool = True
    sentiment_history_lookback: int = 250
    # 增量回补的巡检间隔（秒）：默认 6 小时。回补是增量的（已有日期不重拉），
    # 稳态下每轮只拉 1–2 天 × 2 个请求，配额开销可忽略。
    sentiment_history_backfill_interval_seconds: float = 21600.0
    # 盘中情绪监控（sentiment/intraday_monitor.py）的炸板率告警阈值。
    # 原硬编码 0.40 提升为配置（2026-09-07 P0-3：与 *_bands_json 同纪律，
    # 显式配置压过经验值；注意 AlertRule 存量行的 threshold 是新建时固化的，
    # 改本配置只影响新建规则与监控判定，不会回写已有 DB 行）。
    sentiment_break_rate_threshold: float = 0.40

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

    # ---- 执行闸门与影子持仓（picks-intraday-fusion-assessment P0-A/P0-B）----
    # 执行闸门：9:25 竞价 gap 三态（阈值出处：近 60 交易日 4983 样本分桶实测）。
    # gap ≥ block 禁买（一字/超高开，胜率 12%）；5~block 降级观察；≤ anomaly 异常低开复核
    picks_gate_block_gap: float = 9.5
    picks_gate_observe_gap: float = 5.0
    picks_gate_anomaly_gap: float = -5.0
    # 影子持仓：每交易日晨窗（09:26）把最新组合进 scope=shadow 独立账户模拟执行
    # （先卖昨日持仓，再按执行闸门允许的桶开盘买入）——空仓闸门的 A/B 对照组
    picks_shadow_enabled: bool = True
    picks_shadow_start_minute: int = 9 * 60 + 26   # 竞价结束（9:25）后一分钟
    picks_shadow_end_minute: int = 9 * 60 + 45     # 晨窗截止（错过顺延次日，不追价）

    # ---- ths 涨停原因单点哨兵（P0-B）----
    # 涨停原因/题材标签 100% 依赖 ths（东财 0%，无备源）：交易时段周期探测
    # reason 非空率，低于阈值/连续拉取失败 → AlertEvent 告警（文案含
    # "题材标签可能失效"）；状态随 GET /api/system/providers 可见
    ths_sentinel_enabled: bool = True
    ths_sentinel_interval_seconds: float = 900.0

    # ---- 东财 7x24 快讯流（hotspot-pipeline P0①：G1 全市场快讯源）----
    # 连续竞价 15s / 盘外 60s 轮询东财宏观快讯 → build_event 指纹去重入
    # EventStore（事件 tab 即消费层，不开新存储）；双域 failover；失败不缓存
    # （游标不推进，下轮重拉）。性能红线：单源单端点，抽取纯函数。
    flash_news_enabled: bool = True
    flash_news_interval_seconds: float = 15.0
    flash_news_eod_interval_seconds: float = 60.0

    # ---- 盘中情绪监控（sentiment P2 #14，参考 daben-review）----
    # 交易时段周期探测三类纯规则 P0 事件：高度板(≥4板)炸板 / 炸板率连续破
    # 40% / 指数 15min 急杀（上证-0.8%/创业板-1.2%）→ AlertEvent 告警；
    # 通道 in_app/log/feishu（webhook 未配置时 feishu 显式跳过）
    sentiment_monitor_enabled: bool = True
    sentiment_monitor_interval_seconds: float = 60.0
    sentiment_monitor_channels: str = "in_app,log,feishu"

    # ---- marketdb 盘后增量同步（调研采纳第 4 批运维收尾）----
    # marketdb（DuckDB 日K 仓）是 RPS / tech_score v3 第 8 维的数据地基。
    # 开启后每日盘后自动跑 scripts/sync_marketdb.py（近 10 交易日增量 +
    # 复权重建，子进程隔离），磁盘幂等（当日成功一次即跳过，重启安全）。
    # **默认关**：每日全市场 dump 下载（几十 MB）与 ths 配额开销应由使用者
    # 知情决定；CI / 无 ths_api_key 环境也不应尝试。手动兜底：跑脚本。
    marketdb_sync_enabled: bool = False
    marketdb_sync_hour: int = 16
    marketdb_sync_minute: int = 30
    marketdb_sync_check_interval_seconds: float = 300.0

    # ---- 盘后复盘 Agent ----
    # 分析器：rules（默认，确定性、零成本）| llm（需配 base_url + api_key）
    review_model: str = "rules"
    review_methodology_version: str = "v1"
    # 调度：北京时间几点几分触发收盘复盘
    review_run_hour: int = 15
    review_run_minute: int = 30
    review_scheduler_enabled: bool = True
    review_check_interval_seconds: float = 60.0
    # LLM 后端：openai（默认，HTTP 直连 /chat/completions 兼容端点）
    # | claude_cli（子进程调本机 claude 无头模式，凭据走用户 ~/.claude 配置——
    #   适用于只有客户端受限中转 key 的场景；此时 review/news 各自的
    #   *_llm_base_url/_api_key 可留空，只填 *_llm_model）
    llm_provider: str = "openai"
    # claude_cli 可执行文件路径；留空自动探测（PATH > ~/.nvm/*/bin/claude）
    llm_cli_path: str = ""
    # 网关健康探针（2026-09-06）：定时最小调用体检，把「额度不足」与
    # 「网关失败」分开——两者在系统里都表现为降级到 rules，过去只能人肉分辨。
    # 单次约 $0.0006，默认半小时一拍；设 0 关闭（仍可走端点手动触发）。
    llm_probe_enabled: bool = True
    llm_probe_interval_seconds: float = 1800.0
    # 探针告警：连续失败几次才发、冷却多久、走哪些通道（冷却是硬要求——
    # 定时探针不冷却必然刷屏，同 2026-09-04 定案的"预警无冷却"缺陷）
    llm_probe_alert_after: int = 3
    llm_probe_alert_cooldown_seconds: float = 3600.0
    llm_probe_channels: str = "in_app,log,feishu"
    # 助手受限工具调用（2026-09-06 P0-3）：允许助手按需调用只读数据工具
    # （涨停池/龙虎榜/复盘报告等白名单）。关掉后提示词不再注入工具清单，
    # 模型回到"只有注入快照"的状态——宁可少答，也不让它凭空编。
    assistant_tools_enabled: bool = True

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

    # ---- 告警推送通道：飞书 ----
    # 两条路二选一（都配置时优先 webhook）：
    # A. 群自定义机器人 webhook（https://open.feishu.cn/open-apis/bot/v2/hook/xxx）：
    #    在飞书群「设置 → 群机器人 → 自定义机器人」添加后复制
    # B. 自建应用凭据（app_id + app_secret）直发用户 P2P 私信（OpenAPI
    #    /im/v1/messages），无需建群；notify_open_id 为接收人 open_id（ou_ 开头，
    #    可用 lark-cli auth status 查看）。凭据来自飞书开放平台「凭证与基础信息」。
    # 都未配置 = feishu 通道不可用（规则选了 feishu 会显式 warning 并跳过，不伪装成功）
    alert_feishu_webhook: str = ""
    # 机器人开启「签名校验」时填同款密钥；未开启留空
    alert_feishu_secret: str = ""
    # 自建应用凭据 + P2P 接收人（三件齐备且 webhook 未配置时启用 app 通道）
    feishu_app_id: str = ""
    feishu_app_secret: str = ""
    feishu_notify_open_id: str = ""

    # 盘中 watcher 提醒分发通道（逗号分隔：in_app/log/feishu）。
    # feishu 在列但 webhook 未配置时按通道既有语义显式跳过（warning 日志可见）。
    picks_watcher_channels: str = "in_app,log,feishu"

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
