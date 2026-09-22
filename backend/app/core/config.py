from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[3]

log = logging.getLogger(__name__)

#: 历史短名开关 → (正式字段, 正式环境变量)（R10，2026-09-14）。
#: 生效语义见 `Settings._apply_legacy_toggle_aliases`：短名仍兼容但必告警，**正式名优先**。
#: ⚠️ 这里是短名唯一允许出现的地方（`.env.example` 只登记正式名）。
_LEGACY_TOGGLE_ALIASES: dict[str, tuple[str, str]] = {
    "ASHARE_AGENT_AUTONOMY": ("agent_autonomy_enabled", "ASHARE_AGENT_AUTONOMY_ENABLED"),
    "ASHARE_AGENT_CODE_CHANGE": ("agent_code_change_enabled", "ASHARE_AGENT_CODE_CHANGE_ENABLED"),
}

_TRUTHY = {"1", "true", "t", "yes", "y", "on"}
_FALSY = {"0", "false", "f", "no", "n", "off"}


def _parse_bool_env(raw: Any) -> bool | None:
    """宽松解析布尔环境变量；无法识别返回 None（是否报错交给 pydantic 决定）。"""
    if isinstance(raw, bool):
        return raw
    v = str(raw).strip().lower()
    if v in _TRUTHY:
        return True
    if v in _FALSY:
        return False
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ASHARE_", env_file=".env", extra="ignore")

    app_name: str = "AShare AI Trader"
    version: str = "0.1.0"
    log_level: str = "INFO"

    # 主源 + 备源（逗号分隔）：ths | tencent | sina | eastmoney | mock；mock 只能单独使用
    data_provider: str = "ths"
    provider_fallbacks: str = "tencent,eastmoney,sina"
    # 逐笔的 **TDX 直连降级备源**开关（`IMP-038`，2026-09-16）。
    # 为什么需要这个开关（不是"多一个配置项"）：TDX 走 **TCP 7709 真实网络**，
    # 不是 HTTP provider，**mock 替不掉它**。测试环境若开着，凡链上返回空/异常的用例
    # （如 `tests/test_depth_tools.py` 的 `trades=[]` 桩）都会**真的去连 TDX 服务器**
    # ⇒ 单测变成"有网才过、结果随行情变"。故 conftest 显式置 false
    # （与 `ASHARE_DATA_PROVIDER=mock`、调度器全家桶关闭同一纪律：**测试不得触网**）。
    trades_tdx_fallback_enabled: bool = True
    ths_api_key: str = ""  # 同花顺 fuyao 官方 API Key（放 .env，勿提交）
    ths_base_url: str = "https://fuyao.aicubes.cn"
    # 题材官方成分视为有效的时长（architecture-design §1）；过期后懒同步。
    # 2026-09-08 用户指令（成分与同花顺完全一致）：168h 曾让 stale 判定 7 天不命中
    # ——30min×40 的补齐调度因此空转，成分调整（如代糖概念新增 920230）最长一周
    # 才被发现。24h 与 30min×40 的调度能力（理论 1920 个/天 ≫ 390）匹配。
    theme_members_ttl_hours: int = 24
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

    # ⚠️ 已被 `IMP-028`（09-15）收敛为空转配置：通知中心不再有新闻条目，
    # 该值只是 `/api/notifications` 响应里 `news_min_score` 字段的兼容取值
    #（端点内无任何过滤消费它；旧客户端读到的仍是此默认值）。保留以免配置报错。
    notifications_news_min_score: float = 60.0

    # ---- 盘前简报与盘中跟踪（选股 2.0，批次 B）----
    # 盘前简报：交易日 08:40 自动生成（方向排序 + 标的池 + 触发/证伪条件），
    # 落盘 data/picks/briefs/YYYYMMDD.json；POST /api/picks/morning-brief/generate 可手动重跑
    premarket_brief_enabled: bool = True
    premarket_brief_hour: int = 8
    premarket_brief_minute: int = 40
    # 每日组合自动生成（2026-09-09：生成归后端时钟，推送纪律不变——KB-DEC-001）
    picks_autogen_enabled: bool = True
    picks_autogen_hour: int = 9
    picks_autogen_minute: int = 26
    # RSH-026：候选→硬门→精排点时证据必须由后台自行归档，不能依赖前端 GET。
    # tick 仅检查 snapshot_service.saved_files；重型机会构建只在新持久快照出现时触发
    # （默认 snapshot_save_interval=300s），故 30s 检查不会放大 provider 请求。
    picks_opportunity_evidence_enabled: bool = True
    picks_opportunity_evidence_interval_seconds: float = 30.0
    # 盘中跟踪：以当日简报为跟踪清单，交易时段内按 interval 取拍（ths 涨停池 +
    # 东财板块涨幅），确认/证伪判定走 intraday_rules（与回测同一份代码）
    picks_watcher_enabled: bool = True
    picks_watcher_interval_seconds: float = 60.0
    # 环境缓存（phase + promo 分位）刷新间隔：compute_market_sentiment 较重
    # （全市场宽度 + 两天涨停池），60s/拍全量重算太重且浪费数据源配额
    picks_watcher_env_refresh_seconds: float = 600.0
    # 板块异动检测器（2026-09-13 第一期）：自主发现（不依赖盘前简报登记方向），
    # 数据 = 全市场快照 × 官方题材成分聚合；触发阈值为**初始参数未经实证**
    # （board_surge.py 模块头），样本积累后按 strategy_verify 纪律校准。
    board_surge_enabled: bool = True
    board_surge_interval_seconds: float = 60.0
    board_surge_channels: str = "in_app,log"  # 推送矩阵不改动（飞书盘中只留买点卡，用户 09-08 定稿）
    # 龙虎榜当日归档（P2-37 二期 E4）：交易日 17:05-23:00 每 15 分钟尝试，
    # 落 data/lhb/<date>.json（辨识度画像上榜次数与复盘归因的数据地基）
    lhb_archive_enabled: bool = True
    # 大单异动阈值（亿）：题材成员当日主力净流入**首破**该值 → 盘中提醒（P1-16）。
    # 2026-09-10 源头收紧 0.3 → 1.0：实测原阈值下 151 条事件里 97% 被判读层忽略，
    # 且挤占判读预算导致 falsify/high_board_break 等从未被判读。经验初值，
    # 回测校准后再定稿；非正/非法回退代码内默认并记日志（不静默改口径）。
    picks_flow_surge_yi: float = 1.0
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

    # ---- 相位→风格路由表（审查报告 §4.1，P1-2 规则版）----
    # 六相位 → 六维权重偏移。空串 = 用 picks/style_router.py DEFAULT_ROUTES 默认表；
    # 非空必须是 JSON {"发酵": {"echelon": 0.06, ...}}，仅同名维度覆盖默认表。
    # 非法 JSON / 未知维度 / |delta|>0.06 启动即抛错（fail fast，不静默回退）。
    # 偏移是否真提升胜率待 factor_ic_review 月度复核 + 影子 A/B，初版幅度保守。
    picks_style_offsets_json: str = ""

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
    # 频道（东财 fastColumn，2026-09-10 实测）：100=**全部**（含公司/资金/政策/
    # 市场，已覆盖 101 内容）｜101=要闻｜102/103=公司｜104/110=市场｜105=社会
    # ｜107/111/112/113=国际。默认 100 修正"只拉宏观→盘后公司消息无法关联"的
    # 覆盖缺口（retro-and-gaps P0-2）。多频道用逗号分隔，跨频道按 code 去重。
    flash_news_columns: str = "100"
    # 每频道翻页数（retro P0-2 多页拉取）：东财 getFastNewsList 用 data.sortEnd
    # 游标翻页（实测边界无缝衔接）。默认 2 页 = 100 条/频道，追平冷启动历史；
    # 每轮仍靠 EventStore 指纹去重，翻页只增加覆盖不产生重复。
    flash_news_pages: int = 2

    # ---- 盘中情绪监控（sentiment P2 #14，参考 daben-review）----
    # 交易时段周期探测三类纯规则 P0 事件：高度板(≥4板)炸板 / 炸板率连续破
    # 40% / 指数 15min 急杀（上证-0.8%/创业板-1.2%）→ AlertEvent 告警；
    # 2026-09-08 用户指令：预警/异动类不再推飞书，通道收敛 in_app/log
    sentiment_monitor_enabled: bool = True
    sentiment_monitor_interval_seconds: float = 60.0
    sentiment_monitor_channels: str = "in_app,log"

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

    # ---- 因子 IC 月度复核（S2-11）----
    # 默认开：**自门控**，不会白跑——报告未超 40 天 / 未到 run_day / 今天已尝试过
    # 三者任一命中即跳过；真正触发是「每月一次、分钟级」的 duckdb 全历史扫描。
    factor_eval_enabled: bool = True
    factor_eval_run_day: int = 1
    factor_eval_hour: int = 17
    factor_eval_minute: int = 30
    factor_eval_check_interval_seconds: float = 3600.0

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
    # 2026-09-08 用户指令：系统探针告警不再打扰飞书（in_app/log 留痕）
    llm_probe_channels: str = "in_app,log"
    # 助手受限工具调用（2026-09-06 P0-3）：允许助手按需调用只读数据工具
    # （涨停池/龙虎榜/复盘报告等白名单）。关掉后提示词不再注入工具清单，
    # 模型回到"只有注入快照"的状态——宁可少答，也不让它凭空编。
    assistant_tools_enabled: bool = True

    # LLM 分析器（未配置时自动降级到 rules）
    review_llm_base_url: str = ""
    review_llm_api_key: str = ""
    # ---- 模型名（**全仓唯一写死点**，2026-09-16 用户指令）----
    # 切换模型的唯一方式是改这里或 `ASHARE_REVIEW_LLM_MODEL`（.env 优先）；
    # 所有消费方（进化议程 / 悬浮球 / 告警判读 / 元评估 / 事件辅助 / 代码执行器 /
    # 网关探针）一律读本字段，**不得在别处写字面量**——
    # 守卫 `tests/test_llm_model_single_source.py` 会判红。
    #
    # 为什么默认值不再是空串：空串时 claude_cli 会把 `--model ""` 透传，
    # 由 CLI 侧默认兜底，于是「项目配的模型」与「实际用的模型」不一致，
    # 出问题只能靠人肉分辨。2026-09-16 就是这么炸的：
    # 用户已把本机 claude 默认模型切到 deepseek-v4-flash，而后端 .env 仍写
    # `glm-5.3`，该模型上游已下架 ⇒ 调用**挂起不返回**（实测 150s 无输出，
    # 而 deepseek-v4-flash 3.3s 正常）⇒ 15:45 进化议程失败、告警判读降级
    # 为 `llm_fallback`。写死一个可用默认值 = 少一层"两边不一致"的失败面。
    review_llm_model: str = "deepseek-v4-flash"

    # ---- TypeSafe Jev：窄语义判断 / 路由 / 影子验证 ----
    # 凭据不进 ASHARE_* 配置：统一从进程环境 TYPESAFE_API_KEY / JEV_API_KEY 读取，
    # 便于本机 Keychain、容器 secret 或部署环境注入；绝不写入前端/文档/仓库。
    jev_enabled: bool = True
    jev_base_url: str = "https://api.typesafe.ai"
    # 生产默认跟随最新模型；需要可复现实验时由 ASHARE_JEV_MODEL 固定具体版本。
    jev_model: str = "jev-latest"
    jev_timeout_seconds: float = 8.0
    # 防误把整份日志/源码发给外部服务；超限必须由调用方缩小 state。
    jev_max_state_chars: int = 20_000
    # 只记录 usage 元数据，不记录 state/questions 正文；用于跨重启核算节省率。
    jev_usage_log_enabled: bool = True
    jev_usage_log_path: str = str(REPO_ROOT / "data" / "jev" / "usage.jsonl")
    # 告警判读两阶段：off=不用；shadow=Jev 与 DeepSeek 并跑但不改行为；
    # cascade=高置信 Jev 直接消费，低置信/失败升级 DeepSeek。
    # 默认 shadow：先积累目标域分歧与置信分布，阈值未校准前不改变用户可见结论。
    jev_alert_triage_mode: str = "shadow"
    # 仅 cascade 使用；默认值只是保守占位，正式采用前须由 RSH-030 标注集校准。
    jev_alert_triage_accept_confidence: float = 0.90
    # pending 事件前置层：shadow 只比较；cascade 仅允许高置信“无直接题材催化”
    # 跳过 DeepSeek。非中性事件仍由 DeepSeek 做题材归属，Jev 不自由生成题材名。
    jev_event_aux_mode: str = "shadow"
    # cascade 中若 Noul(存在直接非零题材催化) <= 本值，则按中性收敛。
    # 正式启用 cascade 前须由 RSH-030 人工金标准校准。
    jev_event_aux_neutral_max_noul: float = 0.05
    # AI 助手工具清单路由：shadow 只量路由覆盖；cascade 才缩短提示词里的工具清单。
    # 路由不改变执行白名单；dispatch 仍以 TOOL_SPECS 为唯一授权面。
    jev_assistant_tool_mode: str = "shadow"
    jev_assistant_tool_min_noul: float = 0.60
    jev_assistant_tool_max_groups: int = 3
    # Universal Verification：只判 evidence→claim 支持关系，不做事实检索。
    # 默认 off，先用 RSH-030 金标准验证后再决定是否常开 shadow。
    jev_assistant_verify_mode: str = "off"  # off | shadow
    jev_assistant_verify_max_claims: int = 4
    jev_assistant_verify_max_evidence_chars: int = 12_000

    # ---- P2-3 层1：pending 事件 LLM 辅助判定（app/events/llm_aux.py）----
    # 规则引擎判不出方向（direction=0 / 无方向行）的事件攒批交给 LLM 判一次，
    # 命中写 direction 行（matched_by=llm_aux）+ 全批记 llm_judged_at 防重复。
    # **停机开关**：默认关（保守——LLM 判定有真实成本，需显式开才跑）。
    event_llm_aux_enabled: bool = False
    event_llm_aux_min_batch: int = 2       # 攒批下限（< 此数不值得一次 CLI 冷启动）
    event_llm_aux_max_batch: int = 12      # 单批上限（防一次超长）
    event_llm_aux_age_max_h: float = 5.0   # 只判近 N 小时的事件（窗口内才有判定价值）
    # 常驻低频轮询间隔（秒）：交易时段内自动攒批判定；默认 20 分钟一拍（低成本）。
    event_llm_aux_loop_interval: float = 1200.0

    # ---- 事件采集常驻循环（main.py::event_collector）----
    # 每 1800s 一轮（首轮延迟 45s），盘后轮次附带最近交易日涨停股范围（R6）。
    # **测试必须关掉**：它是此前唯一没有开关的调度器——长生命周期的 TestClient
    # （如会话级 fixture）下会真的跑起来打网络并重复写 event_direction，
    # 2026-09-10 实测撞 UNIQUE(event_id,target_type,target) 并拖垮 3 个无关用例。
    event_collector_enabled: bool = True

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
    # 2026-09-08 用户指令：确认/证伪/大单异动等中间态一律不再推飞书——
    # 飞书只保留盘中买点卡片（picks_buy_point_channels）；本通道保留
    # in_app/log 供系统内留痕与复盘（assistant 工具仍可查询事件）。
    picks_watcher_channels: str = "in_app,log"

    # ---- 盘中买点推送（2026-09-08 用户定稿：唯一保留的盘中飞书推送）----
    # 仅当当日精选标的满足全部判定（置信档≥可执行 + 无红线否决 + 闸门语义
    # 通过 + 现价入买入区间 + 未触涨停区）才推飞书 interactive 卡片
    # （版式与每日精选推送卡完全一致，app/picks/push_cards.py 同函数）。
    # IMP-044 起 channels 是买点外发的唯一权限事实源：默认继续保留历史实际
    # 产品语义（in_app/log + Feishu），但 Feishu 只经 durable Outbox 异步发送。
    # 显式移除 feishu 即真正关闭该外发，不再存在 send_interactive 旁路。
    picks_buy_point_enabled: bool = True
    picks_buy_point_interval_seconds: float = 60.0
    picks_buy_point_channels: str = "in_app,log,feishu"

    # ---- AI 大脑自主进化（docs/summary/ai-evolution.md v2）----
    # 盘后 15:45 自动汇总五路证据（复盘改进项/signal_health/告警判读统计/…）
    # → LLM 生成「今日进化议程」。受限自治默认开启：A 参数实验/B 知识沉淀可自行闭环；
    # C 代码能力仍须管理员通过独立环境变量显式开启，且只提议、不合并。
    # 安全模型=后置守护：证据门槛 + 值域钳制 + 红线 + 预算 + 自动回滚（P1 实验记录本）。
    # **运行默认**：`ASHARE_AGENT_AUTONOMY_ENABLED=1`；紧急停机设 0，届时只生成建议清单，
    # 且调度器的自动转正/自动回滚一并停止（见 `evolution_scheduler` 的授权判据）。
    # C 类代码修改还必须单独显式设置 `ASHARE_AGENT_CODE_CHANGE_ENABLED=1`；关闭时
    # 只保留建议/议程/patch 预览能力，不得修改工作区、提交或合并。
    # ⚠️ 变量名以 `.env.example` 为准（`env_prefix="ASHARE_"` + 字段名大写）；
    # 手册里长期写作 `ASHARE_AGENT_AUTONOMY=0` 的短名**照旧生效但会告警**（R10 兼容层）。
    agent_autonomy_enabled: bool = True
    agent_code_change_enabled: bool = False  # C 类代码执行器须由管理员独立显式开启
    agent_venv_python: str = ""             # 沙箱门禁用的 pytest 解释器（默认 backend/.venv/bin/python）
    agent_evolution_hour: int = 15
    agent_evolution_minute: int = 45
    agent_daily_llm_budget: int = 8        # 每日进化相关 LLM 调用上限（防失控烧钱）
    agent_daily_task_budget: int = 3       # 每日自动执行的改进任务数上限

    @model_validator(mode="before")
    @classmethod
    def _apply_legacy_toggle_aliases(cls, data: Any) -> Any:
        """把历史短名开关映射到正式字段（R10，2026-09-14）。

        背景：`env_prefix="ASHARE_"` 使环境变量名 = `ASHARE_` + 字段名大写，
        即 **`ASHARE_AGENT_AUTONOMY_ENABLED`**（`.env.example` 一直是对的）。
        而代码注释 / `docs/summary/ai-evolution.md` / `docs/kb/04` 长期写作
        **`ASHARE_AGENT_AUTONOMY=0`**（少 `_ENABLED`）⇒ 按说明设置**根本不生效**：
        operator 以为已停机，实际自治仍在跑（审查 R10「配置复现」已复现）。

        语义（fail-closed，三条都不可省）：
        1. **只设短名** → 照旧生效，但打 WARNING 提示改用正式名（不让旧部署静默失效）；
        2. **正式名也设了** → **正式名优先**，短名一律不覆盖即不改写 data；
        3. **两者冲突** → 额外 WARNING 指出以正式名为准。
        第 2 / 3 条是「停机意图不可被陈旧变量推翻」的落地：若让短名覆盖，
        一个遗留在 shell 里的 `ASHARE_AGENT_AUTONOMY=1` 就能把已关闭的能力
        重新打开——这正是要 fail-closed 的方向。

        实测口径见 `tests/test_agent_toggle_switches.py`（`_env_file=None`，
        只依赖进程环境，不复用仓库 .env）。
        """
        if not isinstance(data, dict):
            return data
        for legacy, (field, official) in _LEGACY_TOGGLE_ALIASES.items():
            raw = os.environ.get(legacy)
            if raw is None or not str(raw).strip():
                continue
            parsed = _parse_bool_env(raw)
            if field in data:
                current = _parse_bool_env(data[field])
                if parsed is not None and current is not None and current != parsed:
                    log.warning(
                        "%s=%s 已被忽略：%s=%s 优先（两者取值冲突）。请删除已废弃的短名。",
                        legacy, raw, official, data[field],
                    )
                else:
                    log.warning(
                        "%s 已废弃，请改用 %s（本次两者取值一致，结果不受影响）。",
                        legacy, official,
                    )
                continue
            log.warning("%s=%s 已生效，但该变量名**已废弃**：请改用 %s。",
                        legacy, raw, official)
            data[field] = raw
        return data

    # ---- 访问鉴权（B6 写鉴权 → R22 统一鉴权边界）----
    # 机制不变（一个共享 token，服务器持有、浏览器不持有）；变的是**失败模式**：
    #   local （默认）：假定只在回环上服务 ⇒ token 留空即全放行，本地开发零摩擦；
    #   shared         ：假定会有回环之外的访客 ⇒ token **必配**，未配则**拒绝启动**。
    # ⚠️ 为什么需要 shared（R22 的病灶）：local 姿态下「忘记配 token」与「配好了」
    # 在运行期**长得一模一样**（都返回 200、都无任何日志差异）⇒ 静默全开。
    # 把「忘了配」从**静默全开**变成**起不来**，是本次加固的止险点。
    auth_mode: str = "local"
    # token 留空 = 见上；配好后所有写请求与敏感读请求必须带 X-API-Token 头
    # （由前端服务端反向代理注入，浏览器不持有）
    api_token: str = ""
    # IMP-052：研究候选晋级批准使用独立凭据。默认空=批准写入口禁用（fail closed）；
    # 不得与普通 API token 共用，否则普通写权限就会被误当成独立审批权。
    agent_promotion_token: str = ""

    @property
    def review_dir(self) -> str:
        return str(REPO_ROOT / "data" / "review")

    @property
    def watchlist_symbols(self) -> list[str]:
        return [s.strip() for s in self.watchlist.split(",") if s.strip()]

    @property
    def cors_origin_list(self) -> list[str]:
        return [s.strip() for s in self.cors_origins.split(",") if s.strip()]

    @property
    def auth_required(self) -> bool:
        """是否校验入站凭据。

        `shared` 姿态**恒需要**（这也是它存在的意义：token 漏配时不是"放行"而是
        「启动就失败」，见 `app.core.auth.validate_auth_posture`）；`local` 姿态
        仅在**显式配了 token** 时需要（此时等价于旧 B6 行为，便于只想加一层写保护的
        本地用户，不必理解 mode 概念）。
        """
        return self.auth_mode == "shared" or bool(self.api_token)


settings = Settings()
