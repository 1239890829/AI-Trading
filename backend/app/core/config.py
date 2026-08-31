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
    poll_interval_seconds: float = 5.0
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
