# 候选因子登记册（原 `backend/app/factors/candidates.py`，2026-09-08 审查 P0-3 迁出）

> 迁移原因：登记册零代码引用、纯文档职责，放在 `app/` 会误导读者以为有运行时消费方。
> 内容原样保留（Python 源码形式的数据定义），治理流程见 `factor-lifecycle-governance.md`。

原 docstring：

```python
"""候选因子登记册（docs/factor-lifecycle-governance.md §2.3）。

定位：环节①「挖掘」与环节⑥「更新」的承接物——所有从外部来源/系统内准因子
挖掘出的候选在此登记（含来源、原始定义、数据可行性分级、去重预判），
过 evaluate 评估后 status=evaluated；转正（promoted）即迁入 library.py，
淘汰（rejected）留档，条件不成熟（parked）保留等待数据。

纪律：
- 只登记「有原始定义+来源」的候选，禁止凭空臆造；
- tier 与 data_dep 必须真实（C 层必须指名数据源），不许「先登记再说」；
- 本文件是代码资产（入 git）；评估产物 eval_report.json 是运行时资产（不入 git）。

来源分组清单（2026-09-07 实测扫描 GitHub trading 分组 22 仓）：
- 因子密集：microsoft/qlib、myhhub/stock、sngyai/Sequoia-X、virattt/ai-hedge-fund
- 方法论参考：FinHackCN/finhack、qusong0627/QuantMind、TradingAgents
- 数据接口候选：HiThink-Tech/Financial-API（财报缺口解锁钥匙）、akshare
- 其余 15 仓为数据接口/交易平台/LLM 应用/无关，无固定因子公式（详见制度文档 §2.1）
"""
```

以下为原文件的候选定义数据（语义不变，原样保留）：

```python
from dataclasses import dataclass


@dataclass(frozen=True)
class CandidateDef:
    name: str
    source: str          # 来源 + 代码位置（repo://path#symbol）
    raw_expr: str        # 原始定义（尽量保留原仓语法/参数）
    category: str
    tier: str            # A 立即可评 / B 积累中 / C 缺数据 / D 待落库
    data_dep: str        # B/C/D 层必填：还缺什么、指名来源
    dedup_hint: str      # 同信源预判（实证以 IC 相关 ≥0.70 为准）
    status: str          # mined → evaluated → promoted | rejected | parked
    note: str = ""


#: ---------------------------------------------------------------- A 层已评（本轮转正/淘汰见 eval_report.json，登记留档）
A_EVALUATED_QLIB: tuple[CandidateDef, ...] = (
    # 2026-09-07 全量评估 26 个（qlib Alpha158 kbar/rolling 算子族 + TA-Lib ATR/BIAS），
    # 定义已直接注册进 library.py（expr 适配 marketdb 口径），判定结果见
    # backend/data/factors/eval_report.json 与制度文档 §3.3。此处登记来源追溯。
    CandidateDef(
        "qlib-kbar-family", "qlib://contrib/data/loader.py#get_feature_config",
        "KMID=(c-o)/o; KLEN=(h-l)/o; KMID2=(c-o)/(h-l); KUP/KUP2=(h-max(o,c))/..; "
        "KLOW/KLOW2=(min(o,c)-l)/..; KSFT/KSFT2=(2c-h-l)/..",
        "kbar", "A", "", "vola20/range20（日内波动类）",
        "evaluated",
        "9 式已评（2026-09-07）：klen/kup/klow CONDITIONAL，kmid/kmid2/kup2/klow2/ksft/ksft2 FAIL（单日形态无稳定预测力→组合/事件化是正确用法）",
    ),
    CandidateDef(
        "qlib-rolling-trend", "qlib://contrib/data/loader.py#BETA/RSQR/RESI",
        "BETA=Slope($close,d)/$close; RSQR=Rsquare($close,d); RESI=Resi($close,d)/$close",
        "trend", "A", "", "mom20（趋势与动量同信源）",
        "evaluated",
        "beta20 PASS（ICIR -0.41，red mom20）/resi20 FAIL/rsqr20 FAIL（滚动 IC 0.018→0.048 增强，观察名单）",
    ),
    CandidateDef(
        "qlib-rolling-position", "qlib://contrib/data/loader.py#RSV/IMAX/MAX/MIN/RANK",
        "RSV=(c-Min(low,d))/(Max(high,d)-Min(low,d)); IMAX=IdxMax(high,d)/d; MAX=Max(high,d)/c",
        "price_structure", "A", "", "mom20/gap",
        "evaluated",
        "imax20 PASS（Aroon，IC +0.043 停牌安全）/rsv20 FAIL/max20 FAIL；RANK20 需展开 LAG(1..19) 列，P1 落地",
    ),
    CandidateDef(
        "qlib-rolling-volprice", "qlib://contrib/data/loader.py#CORR/CORD/CNTP/SUMP/SUMD",
        "CORR=Corr($close,Log($volume+1),d); CORD=Corr($close/Ref($close,1),"
        "Log($volume/Ref($volume,1)+1),d); CNTP=Mean($close>Ref($close,1),d); "
        "SUMP=Sum(Greater($close-Ref($close,1),0),d)/Sum(Abs($close-Ref($close,1)),d)",
        "volume_price", "A", "", "cntp20≈sump20 同族；SUMP 与 RSI 同构",
        "evaluated",
        "corr_pv20 PASS（ICIR -0.73 全库最强）/cord20 PASS（red corr_pv20）/cntp20 PASS/sump20 PASS（qlib SUMN=1-SUMP、SUMD=2*SUMP-1 线性冗余未重复评）",
    ),
    CandidateDef(
        "qlib-rolling-volume", "qlib://contrib/data/loader.py#VMA/VSTD/WVMA/VSUMP/VSUMD",
        "VMA=Mean($volume,d)/$volume; VSTD=Std($volume,d)/$volume; "
        "WVMA=Std(|ret|*vol,d)/Mean(|ret|*vol,d); VSUMD=(Σ增量-Σ减量)/(Σ|Δvol|)",
        "volume", "A", "", "amt_ratio（量比同信源）",
        "evaluated",
        "vma20 CONDITIONAL/vstd20 FAIL/wvma20 FAIL/vsumd20 CONDITIONAL（量能方向 ICIR -0.42；VSUMP=1-VSUMN 未重复评）",
    ),
    CandidateDef(
        "talib-atr-bias-std", "myhhub://instock/core/indicator/calculate_indicator.py + qlib#STD",
        "ATR=AVG(TR14)/close（TR 隔夜跳空>±25% 判除权断层 NULL）；BIAS=(c-MA20)/MA20；STD=Std($close,20)/$close",
        "volatility", "A", "", "vola20/range20/mom20",
        "evaluated",
        "atr14 PASS（ICIR -0.48 波动族新代表候选）/bias20 PASS（red imax20）/std20 PASS（red atr14）",
    ),
)

#: ---------------------------------------------------------------- A 层待评（P1 补齐）
A_PARKED: tuple[CandidateDef, ...] = (
    CandidateDef(
        "qlib-alpha158-multiwindow", "qlib://contrib/data/loader.py#rolling",
        "同算子 × 窗口 5/10/30/60（本轮仅评 20 日代表窗）",
        "multi", "A", "", "对应 20 日版",
        "parked",
        "P1：多窗口扫描补全（预期窗口敏感性=稳健性证据）",
    ),
    CandidateDef(
        "talib-indicators-rest", "myhhub://instock/core/indicator/calculate_indicator.py",
        "MFI（资金流量，用 turnover 代理 tp×vol）/OBV 归一/ADX/CCI/PPO/SAR/WILLR/STOCHRSI 等 TA-Lib 25 指标",
        "multi", "A", "", "RSI≈sump20；ADX/CCI 需窗口实现",
        "parked",
        "P1 逐个转正；MFI/OBV 优先（资金流向语义，与 fund_flow 数据互验）",
    ),
    CandidateDef(
        "ta-lib-cdl-patterns", "myhhub://instock/core/pattern/pattern_recognitions.py",
        "TA-Lib CDL 蜡烛图形态（61 种，0/1 事件）",
        "pattern", "A", "", "kbar 族",
        "parked",
        "事件因子通道（§3.4）落地后批量评；形态组合效应 > 单形态",
    ),
)

#: ---------------------------------------------------------------- C 层：数据缺口（数据源扩容后解锁）
C_MISSING_DATA: tuple[CandidateDef, ...] = (
    CandidateDef(
        "pead-earnings-drift", "virattt://ai-hedge-fund/hedge_fund/signals/pead.py + strategies/earnings-drift.yaml",
        "盈余公告后漂移：财报超预期（surprise）后 N 日漂移。经典文献因子",
        "fundamental", "C", "财报数据（业绩快报/预告）：HiThink-Tech/Financial-API 或 akshare stock_yjbb",
        "mom20（事件驱动型动量）",
        "mined",
        "A 股语境：业绩预告/快报日历 + 超预期幅度分位；与财报期 regime 联动（picks/regime.py 已有日历）",
    ),
    CandidateDef(
        "value-family-graham-buffett", "virattt://ai-hedge-fund/hedge_fund/signals/{graham,buffett,munger,lynch}.py",
        "价值族：PE/PB/净流动资产折价（Graham）；ROE+低负债+稳定毛利（Buffett/Munger）；PEG（Lynch）",
        "fundamental", "C", "财务报表数据：同上",
        "无（与价量因子正交——组合价值最高的方向）",
        "mined",
        "需先扩财报数据源；A 股适用性需重审（壳价值/周期股语境）",
    ),
    CandidateDef(
        "turnover-rate-family", "qlib/文献通用",
        "真实换手率及其衍生（换手率分位/换手率变化率）",
        "liquidity", "C", "流通股本：ths fuyao 或 akshare 股本接口",
        "liq20/amihud20（额→真实换手升级）",
        "mined",
        "解锁后 liq20/amihud20 升级为真换手口径（DATA_QUALITY_NOTES['turnover'] 的根治）",
    ),
    CandidateDef(
        "industry-neutral-momentum", "qlib/文献通用",
        "行业中性化动量/低波（剔除行业效应后的个股因子残差）",
        "cross_section", "C", "行业分类：ths 板块成分（已有）或申万行业",
        "mom20/vola20",
        "mined",
        "ths 板块映射已有——可行性高于财报类，P1 可先行",
    ),
)

#: ---------------------------------------------------------------- B/D 层：系统内前向积累（积累满 60 交易日自动可评）
BD_ACCUMULATING: tuple[CandidateDef, ...] = (
    CandidateDef(
        "board-streak-interaction", "系统内://market/board_flow.py（2026-09-07 上线）",
        "板块资金连续流入天数 × 个股动量（mom20）交互",
        "interaction", "B", "board_flow daykline 60 交易日（~2026-12 满足）",
        "mom20/amt_ratio",
        "mined",
        "factor-library-design.md §3.3 结合方向④；到期走事件+RankIC 双通道",
    ),
    CandidateDef(
        "theme-heat-percentile", "系统内://picks/heat_history.py（JSONL 前向积累）",
        "题材热度全市场分位（题材层因子，映射到个股=所属题材热度）",
        "theme", "B", "~30 行/天，2026-12 可评",
        "无（题材层新信息源）",
        "mined", "",
    ),
    CandidateDef(
        "auction-premium", "系统内://market/auction_premium.py（自述因子，未验证）",
        "集合竞价溢价（竞价价/昨收-1）",
        "intraday", "D", "竞价快照落库（D 层积累启动后）",
        "gap（隔夜跳空近亲——竞价溢价=盘中确认版 gap）",
        "mined",
        "首个 D 层验证对象；与 gap 因子交叉验证是制度闭环的第一个实证",
    ),
    CandidateDef(
        "minute-signal-score", "系统内://market/minute_signals.py（docstring 自认未经历史校准）",
        "分时五指标复合分",
        "intraday", "D", "分钟线落库（P1）",
        "无",
        "mined", "",
    ),
    CandidateDef(
        "chain-direction-strength", "系统内://events/chains.py（T8，2026-09-07 上线）",
        "快讯传导链方向×强度（政策/产业链/海外实体三类）",
        "event", "B", "60 交易日（~2026-12）",
        "无（消息面新信息源，与量价因子相关性是预注册假设）",
        "mined",
        "与制度 §7.3 倒计时表联动",
    ),
)

#: ---------------------------------------------------------------- 事件因子（Sequoia 系，评估走 §3.4 事件研究通道）
EVENT_CANDIDATES: tuple[CandidateDef, ...] = (
    CandidateDef(
        "seq-limit-up-shakeout", "sngyai://Sequoia-X/sequoia_x/strategy/limit_up_shakeout.py",
        "涨停洗盘：昨涨停(close≥前收×1.095) + 今收阴(c<o) + 放量2×(v>昨v×2) + 支撑不破(low≥昨收)",
        "event", "A", "", "无",
        "mined",
        "规则零歧义可直接 SQL 化；事件稀疏（预计全市场日均 <5 触发）需长窗口",
    ),
    CandidateDef(
        "seq-high-tight-flag", "sngyai://Sequoia-X/sequoia_x/strategy/high_tight_flag.py",
        "高紧旗形：40 日高低比>1.6（强动量） + 10 日高低比<1.15（收敛） + 今日量<20 日均量×0.6（缩量）",
        "event", "A", "", "mom20/vola20",
        "mined", "欧奈尔 flag 形态量化版；三阈值可作连续因子分解（旗形度=收敛比×缩量比）",
    ),
    CandidateDef(
        "seq-turtle-breakout", "sngyai://Sequoia-X/sequoia_x/strategy/turtle_trade.py",
        "海龟突破：今收>前 20 日 high 最大值 + 成交额>1 亿 + 实体阳线真涨（防诱多）",
        "event", "A", "", "max20（连续版近亲）",
        "mined", "max20 因子是它的连续化形态，双通道互验",
    ),
    CandidateDef(
        "seq-ma-volume-cross", "sngyai://Sequoia-X/sequoia_x/strategy/ma_volume.py",
        "金叉放量：MA5 上穿 MA20 + 今量>20 日均量×1.5",
        "event", "A", "", "vma20/amt_ratio",
        "mined", "经典金叉+量确认；预期与短动量因子高度相关",
    ),
    CandidateDef(
        "seq-rps-breakout", "sngyai://Sequoia-X/sequoia_x/strategy/rps_breakout.py",
        "RPS120≥90 的股票突破平台",
        "event", "A", "", "mom60/RPS（picks/rps.py 同源）",
        "mined", "RPS 系统已实现——该策略是 RPS 的条件化使用样本",
    ),
    CandidateDef(
        "seq-uptrend-pullback", "sngyai://Sequoia-X/sequoia_x/strategy/uptrend_limit_down.py + parking_apron/backtrace_ma250 等 8 策略",
        "上升趋势中涨停回调 / 停机坪 / 回踩 MA250 等（详见各文件 docstring）",
        "event", "A", "", "beta20/max20",
        "mined", "8 策略分批评（P1 事件通道）",
    ),
)

#: 全量候选池（制度文档 §2.3 schema 的物化）
ALL_CANDIDATES: tuple[CandidateDef, ...] = (
    A_EVALUATED_QLIB + A_PARKED + C_MISSING_DATA + BD_ACCUMULATING + EVENT_CANDIDATES
)

CANDIDATE_BY_NAME: dict[str, CandidateDef] = {c.name: c for c in ALL_CANDIDATES}

```
