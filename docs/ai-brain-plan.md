# 新仓库评估 × 自动化精简 × AI 助手大脑化方案

> 2026-09-08 · 基于实测（两仓均实际安装运行验证，非 README 推断）+ 全量代码调研

---

## 一、两个新仓库分析结论

### 1.1 akfamily/akquant 0.3.58 —— **建议引入（回测+指标引擎）**

**定位**：akshare 官方姊妹项目（同属 akfamily 组织），Rust 内核 + Python 接口的量化回测框架，1.5 万行 Python + Rust src。

**实测记录**（隔离 venv，marketdb 真实数据 000910.SZ 十年 2427 根）：

| 验证项 | 结果 |
|---|---|
| 安装 | PyPI wheel 免编译，依赖 polars/plotly/tqdm ✅ |
| 完整回测 | marketdb 2427 bars 首跑 4.2s（含预热）；akshare 249 bars 182ms ✅ |
| 产出 | trades/positions/orders/liquidation_audits/metrics_df/equity_curve 全套 ✅ |
| **RSI 精度** | **Rust 后端 vs TA-Lib C 基准全程零偏差**（唯一正确实现）✅ |
| Python 后端 RSI | 与 ta 同款头部污染（15-60 行偏差 10.29）⚠️ |
| 因子引擎 | `Rank(Ts_Mean(Close,5))` Polars 表达式，16185 行 **6ms**，要求 symbol 列 ✅ |
| ML walk-forward | QuantModel ABC + Sklearn/PyTorch 双 adapter，train_window/rolling_step/增量重训 ✅（未跑训练，接口级确认） |
| akshare 联动契约 | `stock_zh_a_daily` 的 date/OHLCV 列与 `BasePandasFeedAdapter.normalize` 要求**完全吻合**，直喂 run_backtest ✅ |

**风险**：首跑预热开销；回测进度条噪音（CI/后台需静音）；Python 指标后端与 ta 同样有 NaN→0 头部污染——**用指标一律指定 `backend="rust"`**。

### 1.2 bukosabino/ta 0.11.0 —— **不引入（被 akquant 覆盖）**

**定位**：pandas 纯特征工程库，43 指标。

**实测**：SMA/BOLL/MFI 与手写基准一致（1e-14）；**RSI 头部污染实锤**——`diff.where(diff>0, 0.0)` 把头部 NaN 涨跌当 0 参与 Wilder 累积，前 ~100 行不可信（实测偏差 10.29）；性能 38k 行 4 指标 233ms（中等）。

**判定**：指标面被 akquant.talib（103 个、TA-Lib 兼容、Rust 后端精度全对、性能更强）完全覆盖且质量更低；唯一优势零编译依赖，但 akquant 有 wheel。**跳过。**

### 1.3 akquant × akshare × 系统联动方案

```
marketdb (DuckDB 10年日K, 已有) ──┐
akshare (备源拉取/补数据, 已有) ──┼──→ akquant.run_backtest ──→ 胜率/回撤/交易明细
每日精选选股规则 (策略化)        ──┘         (backend venv)         ──→ 复盘报告 / AI 引用
```

三个联动点：
1. **数据直通**：marketdb `daily_k`（thscode/date_ms/OHLCV）rename 后直接喂 `run_backtest`——已实测 2427 根跑通；akshare 同理（CSV/DataFrame 中转均验证）。
2. **指标直通**：tech_score/RPS 等自算指标可逐步迁到 `akquant.talib`（rust 后端），精度对齐 TA-Lib。
3. **假设检验管道**（核心价值）：每日精选/盘中跟踪的选股规则写成 akquant Strategy → 每日盘后自动回测近期胜率 → 复盘报告「策略有效性」章节 + AI 复盘引用——把「选股逻辑对不对」从主观判断变成可验证数据。

---

## 二、自动化任务精简与合并清单

### 2.1 关键事实（决定精简空间）

- **后端已具备「自主监测盘中异动 + 直发飞书」全链路且已运行**：`watcher_loop` 60s 一拍（方向确认/证伪状态机）→ `dispatch_alert` → `NotifierRegistry` → `FeishuNotifier`；`.env` 三凭据（APP_ID/APP_SECRET/NOTIFY_OPEN_ID）**已配置**，走自建应用 P2P 私信（tenant_access_token + OpenAPI），**无需 webhook、无需 lark-cli**（notifiers/feishu.py，2026-09-04 实测语义）。
- watcher 直发是**事件级即时文本**（异动驱动）；automation 卡片是**时点级汇总卡片**（interactive 双列栅格）。两者语义互补而非重复。

### 2.2 七个任务逐一评估

| 任务 | 判定 | 论证 |
|---|---|---|
| 09:26 每日精选卡 | **保留** | 盘前唯一入口；汇总卡片（名单+竞价闸门）是 watcher 事件流不具备的形态 |
| 10:40 盘中确认卡 | **删除** | watcher 已 60s 级实时直发；10:40 快照卡冗余，删除后信息无损 |
| 13:40 盘中确认卡 | **删除** | 同上 |
| 14:40 尾盘定格卡 | **保留** | 唯一保留的盘中时点卡：收盘前 20 分钟的全天定格汇总，事件流没有这个「收盘视角」；脚本时段哨兵兜底 |
| 15:40 每日精选前瞻卡 | **保留** | 次日计划入口，独立价值 |
| 15:45 盘后复盘 | **保留** | 不是数据拉取——是 AI 清单式审计（三态判定/操作审计/准确率台账/改进项 PATCH 处置/T+3 回访），产出后端报告没有的「处置闭环」，不可被调度器替代 |
| 周日仓库周报 | **保留** | 低频、独立域、无重叠 |

**净效果 7→5**。进一步压缩（14:40 也改后端直发 text 汇总）会丢卡片形态，不建议。

### 2.3 「AI 能否替代定时任务」的结论：不是替代，是三层分工

| 层 | 承担者 | 原因 |
|---|---|---|
| 监测层（秒/分钟级） | 规则引擎（watcher_loop/alert_engine，已有） | LLM 每次调用 2-10s+成本，做 60s 循环既贵又慢；阈值判断规则引擎更可靠 |
| 叙事分析层（每日 2-4 次） | 后端 LLM（claude -p 网关，已有通道） | 盘中综述/事件归因/报告研判——把规则产出的数据变成人话与判断 |
| 重审计层（每日 1 次） | WorkBuddy automation agent | 15:45 清单式复盘需要完整工具面（git/pytest/lark-cli/多轮校验），后端子进程给不了 |

---

## 三、AI 助手能力全景图与核心定位

### 3.1 现状边界（代码级调研）

- 调用：`claude -p` 子进程，`--tools ""` 禁用全部原生工具 + `--system-prompt` 三件套（llm_client.py:175）；「工具调用」是**文本标记协议**（回答里写 `{{tool:名称|参数}}` → 后端解析执行 → 二轮生成），每轮 ≤2 次、≤1500 字
- 工具面：10 个只读白名单（quotes/limit_up/limit_down/limit_break/longhu/boards/hot/anomaly/review/brief）
- 上下文注入：仅 ≤6 只标的行情快照；持仓/复盘不主动注入
- 会话：无服务端存储，前端每次全量上传 ≤40 条
- 入口：单一悬浮球，纯问答 + 站内跳转
- LLM 现有消费点仅 4 处：助手、复盘研判（LLMAnalyzer）、新闻摘要（LLMSummarizer）、探针——**系统 90% 的数据资产 AI 触达不到**

### 3.2 目标定位：三层大脑

```
┌─ 数据枢纽层（P0/P1）──────────────────────────────┐
│ 工具白名单扩容：全数据出口接入（持仓/精选/事件/情绪/  │
│ 复盘）；上下文注入扩展到「持仓+今日精选+今日事件」    │
├─ 分析决策层（P1）────────────────────────────────┤
│ 15:35 盘后对照加 LLM 综述（rules 降级已有）；        │
│ 新闻事件→板块/持仓归因；复盘行动项处置建议           │
├─ 自主执行层（P2/P3）─────────────────────────────┤
│ 原生工具调用（--tools "" → 受控 allowedTools）；     │
│ 会话持久化+长期记忆；写操作白名单（批注/注记）；      │
│ assistant skills 目录（markdown 知识包热加载）；     │
│ 选股假设检验官（akquant 回测由 AI 触发与解读）        │
└───────────────────────────────────────────────┘
```

### 3.3 用户未提及、调研发现的职责（按可行性排序）

1. **交互式归因对话**：收盘后问「今天为什么亏/为什么错过」→ AI 直接引用当日复盘报告+watcher 事件流+操作记录回答（P1，只需工具扩容）
2. **报告叙事化**：盘后报告从指标表格变 AI 叙述（P1，LLMAnalyzer 已有雏形）
3. **阈值校准顾问**：定期回顾 watcher 阈值/执行闸门参数的历史命中率，给出校准建议（P2）
4. **选股假设检验官**：akquant 落地后，AI 自主触发回测、解读胜率、在复盘中标注「该逻辑近 20 日胜率仅 40%」（P2，联动方案落地后）
5. **数据质量语义哨兵**：现在 ths 哨兵是纯阈值；AI 可判「数据结构在但语义异常」（如题材归因塌缩成单一未分类）（P2）
6. **跨日关联记忆**：会话持久化后，「上周三同类形态当时怎么走的」可回答（P3）

### 3.4 落地路径

| 批次 | 内容 | 前置 |
|---|---|---|
| **P0（本轮）** | 方案文档 + automation 7→5 + akquant 引入 backend（requirements.lock）+ marketdb→akquant 回测通路验证脚本 | 无 |
| **P1（下一轮）** | assistant 工具白名单扩容（+picks/positions/news/events/sentiment）+ 上下文注入扩展 + 15:35 LLM 盘中综述 | P0 |
| **P2** | 原生工具调用改造（allowedTools 白名单）+ 会话持久化 + skills 目录 + backtest_picks 进复盘报告 + 阈值校准建议 | P1 |
| **P3** | ML walk-forward 接入选股评分 + AI 自主承担部分调度分析任务 | P2 |

**安全边界（P2 原生工具的前置纪律）**：工具白名单只读先行；写操作单独审批并全量留痕；子进程超时与输出上限沿用现有三件套。

---

## 四、本轮已执行（P0）

1. automation 删除 10:40 / 13:40 两个盘中快照卡（watcher 实时直发替代）→ **7→5**
2. akquant 0.3.58 引入 backend 依赖（requirements.lock 同步）+ `backend/scripts/backtest_picks.py` marketdb→akquant 通路验证
3. 本方案文档 `docs/ai-brain-plan.md`

## 五、P1 实施记录（2026-09-08 盘中，用户指令「继续」授权）

- **工具白名单 10→14**：+picks（最近精选组合含置信档）/ positions（持仓+浮动盈亏，缺行情回退成本价口径）/ sentiment（近 5 日相位）/ events（今日 watcher 事件）。只读纪律不变，校验/限额/缓存沿用既有机制。
- **上下文注入扩展**：行情快照之外主动注入「持仓 + 最近精选 + watcher 事件摘要」（紧凑块 ≤1200 字，best-effort 静默降级）；grounding 证据池同步扩展到该块。
- **15:35 LLM 收盘综述**：`GET /api/assistant/daily-summary`（相位/精选/事件/持仓/指数 → 200~300 字叙事），automation `00a60b78` 工作日 15:35 飞书 text 推送。与 15:45 复盘分工：本任务=叙事层，复盘=审计层。LLM 失败 available=False → automation 跳过发送，绝不发占位文。前提核实：09-07 复盘 model_degraded=0（09-04 的 glm-5.3 别名问题已不存在）。

## 六、待用户确认项（先提后做）

- 14:40 尾盘卡是否也要改成后端直发（会从 interactive 卡片降级为 text，不建议）
- P2 批次（原生工具 allowedTools / 会话持久化 / skills 目录 / backtest_picks 进复盘报告）待 P1 运行观察后启动
