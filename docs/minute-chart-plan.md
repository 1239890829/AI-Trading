# 分时图模块优化方案（交互 / 坐标 / 功能 / 做T / 复盘）

> 现状基线（2026-08-30 实测）：`minute-chart.tsx` 仅面积线+单色量能副图；无均价线、无十字光标浮层、无昨收锚定；Y 轴由 lightweight-charts 自动缩放——**一字板时 min==max，曲线悬在中线**（用户反馈实锤）。
> 数据侧：`/api/minute-line` 返回 `{ts, price, volume, cum_amount}`；腾讯 minute/query 响应里的 `data.date`（真实交易日）与 `qt[code][4]`（昨收）**均被丢弃**；后端用 `datetime.now()` 拼时间戳，非交易日请求会把历史数据打上今天日期（隐患）。
> 红线对齐：做 T 信号只输出「偏向+依据+失效条件」（AGENTS 红线 3）；回测口径全部遵循 `docs/backtest-rules.md`（as_of 推进、禁同 bar 收盘价撮合、T+1/涨跌停拦截、成本计入、样本内外分离）。

---

## 模块 0：数据层补强（P0，一切的前置）

**改动点：`/api/minute-line` 响应扩展**

```jsonc
{
  "symbol": "600519",
  "trade_date": "2026-08-28",        // 新增：来自 tencent data.date（修掉 now() 拼 ts 隐患）
  "prev_close": 1292.30,             // 新增：tencent qt[code][4]；快照兜底（Quote.prev_close 已有字段）
  "limit_up": 1421.53,               // 新增：Quote.limit_up_price（快照有则带，无则 null，前端降级）
  "limit_down": 1163.07,
  "points": [{
    "ts": "...", "price": 1293.46, "volume": 43400,
    "cum_amount": 55993745.0,
    "cum_volume": 51500,             // 新增：累计量（后端算）
    "avg": 1292.11                   // 新增：均价线 = cum_amount / cum_volume（口径唯一，后端算）
  }]
}
```

- 均价线不需要新数据源：`avg_i = cum_amount_i / cum_volume_i`，金融软件均价线即此定义。
- ts 修复：`data.date` + 行内 HHMM 拼 UTC isoformat，废弃 `datetime.now()`。
- 昨收兜底链：qt 数组 idx4 → QuoteHub 快照 `prev_close` → null（前端降级为普通自适应坐标）。
- 工作量：0.5 天（tencent.py 解析 + schema + mock provider 同步 + 测试）。

---

## 模块 1：光标交互（P0）

**结构**：lightweight-charts 原生 `subscribeCrosshairMove`（触摸/鼠标统一触发，移动端天然生效）→ 自绘绝对定位浮层 div。

**计算逻辑**（纯函数，无状态）：

```ts
// lib/minute-tooltip.ts
function tooltipAt(points: MinutePoint[], prevClose: number, ts: number): MinuteTooltip | null
// 由 param.time 二分/索引查找最近 point，派生：
interface MinuteTooltip {
  time: string;        // HH:MM
  price: number;
  changePct: number;   // (price - prevClose) / prevClose * 100，红涨绿跌
  changeAbs: number;
  avg: number;         // point.avg
  avgDevPct: number;   // (price-avg)/avg*100  ← 做T核心观察量，浮层直接给
  vol: number;         // 该分钟成交量（手）
  cumAmount: number;   // 累计成交额（亿元格式化）
}
```

**交互细节**：
- 浮层跟随十字光标，左半屏放右侧、右半屏放左侧（防遮挡）；`pointer-events: none`。
- `crosshair: { mode: CrosshairMode.Normal }`（自由点位而非吸附）。
- 无数据点（午休/触摸空白区）浮层隐藏，不残留。
- 触摸端：LHC 的 touch 事件已驱动 crosshair；浮层字号加大一档，按压移动实时更新，抬手保留 2s 后淡出。

**工作量**：0.5 天（含移动端验证截图）。

---

## 模块 2：坐标系修正（P0，用户反馈的 bug 在此）

**根因**：Y 轴全权交给库自动缩放（`minute-chart.tsx` 未传任何 scale 配置）。一字板时数据 min==max，LHC 对零跨度序列给默认对称区间 → 曲线悬中线。

**修复逻辑**：

```ts
// 以昨收为中心的对称 range；half 有地板价防止平淡走势时抖动
const half = Math.max(
  Math.abs(dayHigh - prevClose),
  Math.abs(dayLow - prevClose),
  prevClose * 0.005            // 0.5% 地板：横盘日不让曲线撑满全屏
);
series.applyOptions({ autoscaleInfoProvider: () => ({
  priceRange: { minValue: prevClose - half, maxValue: prevClose + half } }) });
```

- **涨跌停贴边**：对称区间天然保证——涨停价 = prev×(1+10%) 时偏离即 half，曲线顶到上边界。不需要把 limit_up 塞进 range（那会在非涨停日把图压扁）。
- 昨收基准虚线：`series.createPriceLine({ price: prevClose, color: zinc, lineStyle: dotted })`。
- **双侧标注**：右轴显示绝对价格（原生）；左轴挂一条隐藏 series，`value = (price/prevClose-1)*100`，`leftPriceScale: { visible: true }` + `priceFormat: { type: "percent" }` → 左轴即涨跌幅刻度。此左轴同时为模块 3 的大盘叠加（同为 % 口径）与做 T 浮层提供统一坐标。
- 昨收为 null（数据缺失）时整体降级回库默认自适应，浮层涨跌幅字段显示 `--`，不猜。

**工作量**：0.5 天。模块 0+1+2 合计约 1.5 天，一次交付，分时图质变。

---

## 模块 3：功能补全清单（对照通达信/东财/同花顺）

| 要素 | 现状 | 接入方式 | 优先级 |
|---|---|---|---|
| 均价线（黄线） | ❌ | 模块 0 已算 avg，加一条 LineSeries（黄色 #eab308） | **P0** |
| 昨收基准+双轴 | ❌ | 模块 2 | **P0** |
| 量能柱红绿分色 | 单色 | 按分钟价 vs 前分钟价染红/绿（零成本） | P1 |
| 量比（近似） | ❌ | `量比 ≈ 当日累计量 / (昨全日量 × 时间进度)`；昨全日量从日 K/快照取。精确口径需 5 日同期分钟量（见下） | P1 |
| 集合竞价点 | ✅ | 已实现（2026-08-30）：ths auction snapshot，09:25 金色单点 + 角标（竞价价纳入对称区间防裁剪）；量比角标旁展示竞价涨跌幅 | P1 |
| 分时成交额 | 部分（累计额有） | 增量额 = cum_amount 差分，可选第二副图或浮层展示；明细仍是逐笔页职责 | P1 |
| 大盘叠加对比 | ❌ | 叠加 sh000001 归一化 % 曲线（第二条 series，共享左轴 %）；一次额外 minute-line 请求 | P1 |
| 量比（精确） | ❌ | 需近 5 日分钟数据。方案：Parquet 快照已有 5550 只全市场时点数据，按日聚合 5 日同期均量落内存缓存；或腾讯 minuteK 多日拉取 | P2 |
| 五档盘口联动 | 分离 | 右栏已有盘口 tab；联动=浮层追加"当前五档"摘要。跨组件通信成本 > 收益，先不做，浮层内展示即可 | P2 |
| 尾盘异动/大单标记 | ❌ | 依赖逐笔（东财 details 本机限流），数据不稳，暂缓 | P2 |

P1 组工作量约 1.5~2 天；P2 视数据可得性另排。

---

## 模块 4：做 T 辅助——信号引擎（P2，独立排期，最大块）

**实现位置**：后端唯一实现 `app/market/minute_signals.py`（纯函数），暴露 `GET /api/minute-signals/{symbol}`；前端只渲染，**不双端实现**（口径唯一原则）。

**输出契约（红线合规）**：只输出「偏向 + 依据 + 失效条件 + 置信度」，绝不输出确定性买卖结论：

```python
class MinuteSignal(BaseModel):
    ts: str                      # 触发时刻
    bias: str                    # "低吸偏向" | "高抛偏向" | "观望"
    score: float                 # -1.0 ~ +1.0（负=低吸方向，正=高抛方向）
    confidence: str              # low | medium | high（|score|>=0.5 high, 0.3~0.5 medium）
    triggered: list[IndicatorHit]  # 触发的指标及依据数值
    invalidate_condition: str    # 失效条件，必填
    signal_price: float

class IndicatorHit(BaseModel):
    key: str; weight: float
    trigger_value: float; threshold: float
    direction: int               # -1 低吸方向 / +1 高抛方向
    evidence: str                # 人话依据，如"偏离均价 -1.8%，连续 3 分钟不再新低"
```

**指标与权重（初版，未经历史校准，须按模块 4.3 回测后调参）**：

| # | 指标 | 权重 | 触发条件 | 失效条件 |
|---|---|---|---|---|
| 1 | 均价线偏离 | 0.30 | dev=(p-avg)/avg ≤ −1.5% 且随后 3 分钟不再创新低 → 低吸偏向；dev ≥ +2%（阈值按近 5 日日波动率自适应：base × (1 + vol/2σ)）→ 高抛偏向 | dev 破 −3% 且量比 >1.5：视为出货，低吸偏向作废 |
| 2 | 量价背离 | 0.25 | 价创新高但分钟量 < 前 5 分钟均量×0.6 → 顶背离（高抛）；价创新低但量缩 ≥40% → 底背离（低吸） | 后续 5 分钟内放量（>均量×1.5）同向突破，背离失效 |
| 3 | 分时量能突变 | 0.20 | 单分钟量 > 当日已过均量×3：向上→拉升确认（强化既有偏向，不单独开仓）；向下→下杀预警 | 下一根分钟量 < 均量（脉冲后无延续） |
| 4 | 盘中新高/新低突破 | 0.15 | 突破开盘 30 分钟高点且量比 ≥1.2 → 强势确认（抑制高抛偏向）；跌破开盘 30 分钟低点 → 偏空 | 收回区间内（假突破） |
| 5 | 换手率进度 | 0.10 | 累计换手 ≥ 近 5 日日均换手×0.8 且价格滞涨（距当日高点 <0.5%）→ 筹码充分换手，高位预警 | 价格再创新高则失效 |

**组合规则**：`score = Σ weight × direction(±1)`，方向相反的指标相互抵消；`|score| ≥ 0.5` 出偏向提示，`0.3~0.5` 观察区（记录不出提示）；任何单一指标触发但组合分不足时记为"观察样本"（模块 5 仍记录，供回测统计单指标贡献）。

**数据依赖**：当日分时（有）+ 昨收（模块 0）+ 实时换手（快照 turnover_rate × 流通股本）+ 近 5 日日波动率（日 K 已有）+ 5 日同期分钟量（精确量比，可先缺省——缺该输入时指标 2/4 的量比条件降级为当日均量口径并标注 `degraded`）。

### 4.3 历史准确率回测口径（对齐 docs/backtest-rules.md，全部代码级校验）

- **推进方式**：逐分钟 as_of 推进，信号只读 ≤t 数据；均价/累计量严格从头累计（结构上杜绝未来函数）。
- **撮合**：触发后**下一分钟**成交，价格 = 下一分钟 VWAP（cum_amount 差分 / cum_volume 差分）；禁用触发分钟价格。涨停价不撮合买、跌停价不撮合卖、T+1 约束当日买入不可卖（做 T 场景=底仓高抛低吸，卖出消耗底仓可用量）。
- **成本**：佣金万 2.5（最低 5 元）+ 卖出印花税 0.05% + 滑点 2bp，全计入。
- **有效定义**：信号方向正确且 30 分钟窗口内最优价差 ≥ 2×成本（约 8bp）→ "有效"；方向反 → "错误"；未达阈值 → "无效"。**三分类，不只报胜率**（无效信号也消耗关注）。
- **样本与切分**：近 60 个交易日 × 样本池（自选股 + 每日涨停池成分，含退市不加权）；前 40 日样本内调参、后 20 日样本外验证，**禁止先看结果再调参**（无样本外不报告）。
- **输出**：总命中率 / 平均有效价差 / 三分类分布 / 按情绪阶段（用现有 sentiment phase 分组：分歧期做 T 命中率大概率低于发酵期——这本身就是元结论）/ 按指标 leave-one-out 贡献度。
- **数据底座**：Parquet 快照（5 分钟粒度，5550 只）做粗筛 + 腾讯 minuteK 按日回拉 1 分钟数据（60 日 × 样本池，一次性落 `data/parquet/minutes/`，回测离线跑，不压在线源）。

---

## 模块 5：复盘机制——决策链记录（P2，与复盘 Agent 打通）

**记录时机三段式**：信号触发即记录 → 执行时关联（可选）→ 30 分钟窗口自动结算归因。

```python
class MinuteDecisionRow(Base):            # 新表 review 库同款模式（结构化列 + payload JSON）
    __tablename__ = "minute_decisions"
    id: int
    decision_id: str                      # MD-YYYYMMDD-symbol-seq
    trade_date: str; symbol: str
    signal_price: float                   # 触发时价格
    bias: str; score: float; confidence: str
    triggered: str                        # JSON: IndicatorHit 列表（含各自触发值/阈值/方向）
    executed: int                         # 0|1
    order_id: int | None                  # 关联 paper 订单（place_order 可选参数 signal_id 传入）
    executed_price: float | None
    # —— 结算字段（30 分钟窗口到点由后台任务写回）——
    best_price: float | None              # 窗口内最优价（按 bias 方向取极值）
    worst_price: float | None
    optimal_spread_pct: float | None      # (best-signal)/signal——错过/可得的价差
    realized_spread_pct: float | None     # 实际成交 vs 信号价
    outcome: str                          # correct | wrong | expired
    error_attribution: str                # JSON: 归因结果
    created_at: datetime; settled_at: datetime | None
```

**归因算法（leave-one-out，可解释）**：
1. 结算时若 `outcome=wrong`，对每个已触发指标 i：剔除 i 后重算 score（用触发时刻快照数据，as_of 保证可重放）。
2. 若剔除某指标使 score 方向翻转或 |score| 跌破 0.3 → 该指标记为**主因**，写入 `error_attribution`：`{primary_cause: "量价背离", counter_evidence: "剔除后 score=+0.2，原 score=-0.6", market_phase: "分歧"}`。
3. 全部指标都指向同方向仍错 → 归因为"市况逆风"（记录当时情绪阶段，供方法论迭代：该阶段是否应禁用做 T 信号）。

**与复盘 Agent 集成**（已有 `app/review/` 直接消费）：
- `review/collector.py` 增加 `collect_minute_decisions(trade_date)` → 复盘报告 trades 维度新增"做 T 信号质量"小节：当日信号数、三分类分布、平均错过的最优价差、归因主因 Top3。
- `review/methodology.py` 的元结论闭环：按「指标 × 情绪阶段」统计历史错误率 → 错误率 >60% 的组合自动产出改进项（"分歧期量价背离信号历史错误率 71%，建议阈值收紧或停用"）——这正是方法论自我迭代要吃的输入。
- 执行链缺口的最小改法：`POST /api/paper/orders` 增加可选 `signal_id`，engine 落单时存到 PaperOrder 备注列（加一列，alembic 迁移），复盘才能把"信号→执行→结果"串成完整链条。

---

## 落地顺序与工作量

| 批次 | 内容 | 工作量 | 交付效果 |
|---|---|---|---|
| **P0** | 模块 0 数据层 + 模块 2 坐标修正 + 模块 1 光标浮层 + 均价线 | ~1.5 天 | 分时图达到成熟软件基线：昨收锚定、涨跌停贴边、双轴、悬停全信息、移动端可用 |
| **P1** | 量能红绿分色、量比（近似）、集合竞价点（ths auction）、大盘叠加、分时成交额展示 | ~2 天 | 对齐主流软件功能面 |
| **P2** | 做 T 信号引擎 + 60 日回测 + 决策链记录 + 复盘集成 | ~3.5 天 | 可验证、可归因、可迭代的做 T 辅助（红线合规） |

**需要确认的假设**：① 做=T 仅面向已有底仓的高抛低吸（T+1 合规），不支持当日开平；② 精确量比的 5 日分钟历史允许从腾讯按日回拉落盘（一次性 ~60 日 × 样本池的离线任务）；③ P2 的信号阈值初版未校准，上线前必须跑完样本外验证，不接受"先上再调"。
