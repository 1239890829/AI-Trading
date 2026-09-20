"""盘中机会视图（选股 2.0 呈现层增强，2026-09-03）：先题材机会，后题材内候选个股。

与 /api/themes（题材梯队看板）的关系：机会视图**复用** build_theme_board 的产物，
只在其上追加「辨识度 / 确定性」两个可解释判定，不重建题材逻辑——已有轮子改进，
不重复造。

设计红线（AGENTS.md + 判定类字段三态纪律）：
- 一切结论 = 等级 + 真实依据，**绝不输出买卖建议**；等级只有 高/中/低，
  外加 ``unknown``（数据不可用时如实降级，绝不默默给"低"假装没风险）；
- 评分规则是显式 if 链 + 阈值常量，依据文案拼接真实数据，可追溯、可回测、可调参；
- 辨识度回答「这只股在市场上有没有名气/是不是眼里的核心」（人气 + 高度 + 角色代理），
  确定性回答「题材与个股状态能否支持延续预期」（阶段基座 + 封板质量修正），
  两者是不同维度，禁止合并成一个分数——合并即丢失可解释性。

**2026-09-15 口径变更（用户指令：猎场只收可参与的个股）**：

本模块的两类输出此后**分工明确**，不再混在同一层里：

| 输出 | 内容 | 性质 |
|---|---|---|
| `themes[].stocks` | 当日曾封板梯队（封板历史质量） | **历史参考**——current 封板状态另由 seal_state/tradability 判 |
| `themes[].participants` | 题材内**当前未封板**且通过联动门槛的股票 | **猎场候选**——可进入参与评估，不保证成交 |

两者都由 `top_watch_stocks` 汇总：`items` 取 participants（可参与），
`reference_items` 取涨停梯队（仅参考）。`stocks` 每只额外带 `tradability`
（`picks/tradability.assess`），把"这只票此刻能不能买"显式写在卡片依据里。
判定与挖掘规则见 `app/picks/tradability.py` 模块头（政策原文与病灶分析）。

TODO（诚实口径）：阈值（人气榜名次边界、封单额下限、联动区间的涨幅/成交额门槛）
是经验初值，未经回测校准；调参时以 basis 文案里的证据回放历史，
**分别**统计「可参与候选组」与「仅参考组」的 T+1 胜率后定稿——两组不可混算。
"""
from __future__ import annotations

from app.picks.tradability import (
    MIN_THEME_LIMIT_UPS,
    MIN_THEME_SHARE,
    PER_THEME,
    is_concentrated,
    linkage_candidates,
)

# ---- 可调参数（集中一处，回测校准唯一入口）--------------------------------

#: 人气榜名次 → 辨识度档位边界。ths 热股榜为 24 小时榜，前 30 有效。
HOT_TOP_HIGH = 10
HOT_TOP_MID = 30
#: 无人气数据时，靠连板高度兜底的档位边界
BOARDS_HIGH = 5
#: 封单额（元）与「封单额/成交额」的强封下限；两者满足其一即视为封板质量过硬
SEAL_AMOUNT_STRONG = 1.0e8
SEAL_RATIO_STRONG = 0.10
#: 首封时间（HH:MM）边界：早板加分、尾盘板减分
SEAL_EARLY = "10:30"
SEAL_LATE = "14:00"

#: 题材阶段 → 个股确定性基座（题材在哪，个股的延续预期就在哪）
STAGE_CERTAINTY_BASE = {
    "发酵": "高",
    "启动": "中",
    "高潮": "中",
    "分歧": "低",
    "退潮": "低",
}

#: 盘中跟踪「最推荐标的」默认上限（工作台动态分组容量；题材 2~5 天周期下
#: 8 只足够覆盖核心梯队，再多就稀释「最推荐」语义）
TOP_WATCH_LIMIT = 8

#: 角色自带辨识度加持（梯队地位 = 被记住的成本）
ROLE_NOTABLE = {"空间板", "龙头", "中军", "反包"}


def distinctiveness(
    *,
    hot_rank: int | None,
    hot_available: bool,
    boards: int,
    role: str,
) -> dict:
    """个股辨识度判定：人气榜名次 × 连板高度 × 梯队角色，三态输出。

    hot_available=False（热股榜源失败）时整体 unknown——此时只有高度/角色，
    人气维度缺失会让「高辨识」系统性低估，如实降级优于给错档位。
    """
    evidence: list[str] = []
    hit_high = False
    hit_mid = False

    if hot_available and hot_rank is not None:
        evidence.append(f"人气榜第 {hot_rank} 名")
        if hot_rank <= HOT_TOP_HIGH:
            hit_high = True
        elif hot_rank <= HOT_TOP_MID:
            hit_mid = True
    elif hot_available:
        evidence.append("未上人气榜前 30")

    if boards >= BOARDS_HIGH:
        evidence.append(f"{boards} 板高度")
        hit_high = True
    elif boards >= 2:
        evidence.append(f"{boards} 板")
        hit_mid = True

    if role in ROLE_NOTABLE:
        evidence.append(f"角色 {role}")

    if not hit_high and (hit_mid or role in ROLE_NOTABLE):
        level = "中"
    elif hit_high:
        level = "高"
    else:
        level = "低"

    if not hot_available and level != "高":
        # 人气缺位：除非高度本身够硬，否则不冒充完整判定
        return {"level": "unknown", "basis": "人气榜不可用，辨识度判不完整（" + "；".join(evidence) + "）"}
    return {"level": level, "basis": "；".join(evidence) if evidence else "首板且无人气数据"}


def certainty(
    *,
    theme_stage: str | None,
    formation: str | None,
    has_succession: bool | None,
    break_count: int | None,
    seal_amount: float | None,
    amount: float | None,
    first_seal_time: str | None,
) -> dict:
    """个股确定性判定：题材阶段基座 × 封板质量修正，三态输出。

    基座来自题材（阶段/成建制/梯队接续），修正来自个股（炸板/封单/首封时间）。
    题材阶段缺失时整体 unknown——不确定题材在哪就把个股说死是越权。
    """
    base = STAGE_CERTAINTY_BASE.get(theme_stage or "")
    if base is None:
        return {"level": "unknown", "basis": f"题材阶段缺失（{theme_stage!r}），确定性无法判定"}

    evidence: list[str] = [f"题材{theme_stage}"]
    if formation:
        evidence.append(formation)
    if has_succession:
        evidence.append("梯队有接续")
    if has_succession is False and theme_stage in ("发酵", "高潮"):
        evidence.append("梯队断层")

    seal_ratio = None
    if seal_amount is not None and amount:
        seal_ratio = seal_amount / amount if amount > 0 else None
    strong_seal = (
        (seal_amount is not None and seal_amount >= SEAL_AMOUNT_STRONG)
        or (seal_ratio is not None and seal_ratio >= SEAL_RATIO_STRONG)
    )
    if break_count is not None and break_count > 0:
        evidence.append(f"炸板 {break_count} 次")
    if seal_amount is not None:
        evidence.append(f"封单 {seal_amount / 1e8:.1f} 亿")
    if first_seal_time:
        evidence.append(f"首封 {first_seal_time}")

    # ---- 修正链（显式 if，顺序即优先级）----
    if break_count is not None and break_count >= 2:
        level = "低"  # 反复炸板压过一切题材基座
    elif base == "低":
        level = "低"
    elif strong_seal and (first_seal_time or "") <= SEAL_EARLY and not break_count:
        level = "高"  # 早板 + 强封 + 未开板：题材不弱即给高
    elif base == "高" and (break_count == 0 or break_count is None) and not (
        (first_seal_time or "") >= SEAL_LATE
    ):
        level = "高"
    elif (first_seal_time or "") >= SEAL_LATE and not strong_seal:
        level = "低"  # 尾盘弱封
    else:
        level = "中"

    return {"level": level, "basis": "；".join(evidence)}


def _opportunity_layer(t: dict, perf: dict) -> str:
    """机会三层判定（需求 4，规则可解释）：

    - today_strongest 今日最强：成建制（≥5 家）或已现高度（≥2 板）的领涨/强势题材
    - quiet_starting 悄悄启动：2-4 家、以首板为主、阶段在启动/发酵——正在成形
    - brewing 孕育待发酵：零散（≤1 家）但有题材容器——待事件/资金确认
    """
    count = perf.get("limit_up_count") or 0
    boards = perf.get("max_boards") or 0
    tier = t.get("strength_tier") or ""
    if count >= 5 or boards >= 2 or tier == "领涨":
        return "today_strongest"
    if count >= 2:
        return "quiet_starting"
    return "brewing"


def assemble(
    board: dict,
    hot_stocks: list[dict],
    hot_available: bool,
    *,
    top_themes: int = 5,
    stocks_per_theme: int = 8,
) -> dict:
    """题材梯队看板 + 热股榜 → 机会视图 payload（纯函数）。

    :param board: build_theme_board 产物（themes/ladder/summary）
    :param hot_stocks: 热股榜归一化行（rank/symbol/name/heat），失败时传 [] 且 hot_available=False
    :param top_themes: 取强度前 N 个题材
    :param stocks_per_theme: 每题材取梯队前 N 只（连板降序）
    """
    hot_by_symbol = {s["symbol"]: s for s in hot_stocks}

    themes_out: list[dict] = []
    for t in board.get("themes", [])[:top_themes]:
        perf = t.get("performance") or {}
        stocks: list[dict] = []
        for rung in (t.get("ladder") or [])[:stocks_per_theme]:
            sym = rung.get("symbol")
            hot = hot_by_symbol.get(sym)
            stocks.append(
                {
                    "symbol": sym,
                    "name": rung.get("name"),
                    "role": rung.get("role"),
                    "boards": rung.get("boards"),
                    "change_pct": rung.get("change_pct"),
                    # 官方涨停原因直接取**本行自己**的 reason。曾绕道
                    # leaders.candidates 建 symbol→reason 映射，但 candidates 只含
                    # 「补涨/反包」两种角色（theme_service 中为「低位替代」语义槽）、
                    # 值是硬编码文案 ⇒ 其余角色（龙头/中军/空间板/首板…）全部取不到，
                    # 卡片「入选原因」恒空（2026-09-10 用户反馈）。
                    "reason": rung.get("reason"),
                    # 首封时间此前**只喂给 certainty 判定、没透出到输出**（上方的
                    # `certainty(first_seal_time=rung.get(...))`）。2026-09-15 起它是
                    # 「开盘即涨停」这条可参与性判据的唯一依据 —— 不透出，卡片就只能
                    # 说"已封板"而说不出用户点名的"开盘就买不进"（实测暴露）。
                    "first_seal_time": rung.get("first_seal_time"),
                    "hot_rank": (hot or {}).get("rank"),
                    "distinctiveness": distinctiveness(
                        hot_rank=(hot or {}).get("rank"),
                        hot_available=hot_available,
                        boards=rung.get("boards") or 0,
                        role=rung.get("role") or "",
                    ),
                    "certainty": certainty(
                        theme_stage=t.get("stage"),
                        formation=t.get("formation"),
                        has_succession=perf.get("has_succession"),
                        break_count=rung.get("break_count"),
                        seal_amount=rung.get("seal_amount"),
                        amount=rung.get("amount"),
                        first_seal_time=rung.get("first_seal_time"),
                    ),
                }
            )
        # 涨停梯队的可参与性标注**不在这里**：它需要实时盘口（炸板股是买得进的，
        # 一律写"买不进"是过度断言），而盘口在路由层。故由路由的
        # `tradability.attach_tradability(stocks, snapshot_by)` 统一补，
        # 保证"参考区为什么标不可参与"只有一个判据来源。
        themes_out.append(
            {
                "theme": t.get("theme"),
                "stage": t.get("stage"),
                "stage_basis": t.get("stage_basis") or [],
                "strength_score": t.get("strength_score"),
                "strength_tier": t.get("strength_tier"),
                "tier_basis": t.get("tier_basis"),
                "formation": t.get("formation"),
                "health_note": t.get("health_note"),
                "risks": t.get("risks") or [],
                "max_boards": perf.get("max_boards"),
                "limit_up_count": perf.get("limit_up_count"),
                "has_succession": perf.get("has_succession"),
                # 机会三层（2026-09-08 用户需求 4）：今日最强 / 悄悄启动 / 孕育待发酵
                # 规则可解释：家数+高度+阶段+强度档；判定依据落 basis
                "opportunity_layer": _opportunity_layer(t, perf),
                "layer_basis": (
                    f"涨停 {perf.get('limit_up_count')} 家 · 最高 {perf.get('max_boards')} 板 · "
                    f"阶段 {t.get('stage')} · 强度 {t.get('strength_tier')}"
                ),
                "stocks": stocks,
            }
        )

    summary = board.get("summary") or {}
    return {
        "trade_date": board.get("trade_date"),
        "themes": themes_out,
        "summary": {
            "limit_up_total": summary.get("limit_up_total"),
            "market_max_boards": summary.get("market_max_boards"),
            "top_theme": summary.get("top_theme"),
        },
        "hot_available": hot_available,
        "caveats": list(board.get("caveats") or [])
        + (["热股榜不可用：辨识度仅按高度/角色判定"] if not hot_available else []),
    }


def attach_participants(
    themes: list[dict],
    *,
    snapshot_by: dict[str, dict],
    ever_sealed_symbols: set[str] | None = None,
    limit_up_total: int | None = None,
    members_by_code: dict[str, list[str]] | None = None,
    per_theme: int = PER_THEME,
    snapshot_state: str | None = None,
    snapshot_as_of: str | None = None,
) -> dict:
    """给题材卡片补 `participants`：该题材内**当前未封板**、通过联动与流动性门槛的评估候选。

    **口径变更（2026-09-15 用户指令）**：卡片原来的 `stocks` 逐条是涨停梯队成员——
    旧口径把“今日进入涨停池”直接等同于“当前仍封板”，会把历史身份误当 current 状态。
    现在涨停梯队退居**参考信息**，真正进入猎场候选的是 `participants`：
    题材内当前未封板、且已见联动迹象（涨幅/成交额达标）的个股；它们只进入参与评估，盘口深度/排队仍需后续执行链复核。

    只对**涨停集中**的题材挖掘（`tradability.is_concentrated`）——用户指令是
    "若多数涨停个股集中在同一板块或同一题材概念，则进一步挖掘…"；单家涨停的
    题材没有联动可言，硬挖只会得到噪音。

    容器用卡片上**已经挂好的** `catalog_code`（`attach_official` 的成分重叠挂靠
    产物）⇒ "卡片上显示的官方概念"与"挖掘用的容器"必然是同一个，不会口径分裂。

    每张卡的挖掘结果写回两个键：
    - `participants`：候选列表（`tradability.linkage_candidates` 产物，含 basis）
    - `participants_note`：未挖掘/挖空时的**原因**（三态纪律：空列表必须能区分
      "没有合格候选" 与 "压根没挖"，否则两种情形在页面上长得一模一样）

    :param ever_sealed_symbols: 当日曾进入涨停池的成员——历史身份，不等于当前仍封板
    :param limit_up_total: 当日涨停总数（集中度分母；None/0 ⇒ 只按家数判）
    :param members_by_code: 官方题材 code → 成分 symbol（`board_surge.build_theme_index`
        + `tradability.index_views` 的产物，由路由预取）
    """
    ever_sealed_symbols = set(ever_sealed_symbols or set())
    members_by_code = members_by_code or {}
    mined = 0
    total = 0
    blocked = 0
    blocked_labels: dict[str, int] = {}
    missing_quote = 0
    opened_after_seal = 0
    current_sealed = 0
    current_unknown = 0
    for t in themes or []:
        count = int(t.get("limit_up_count") or 0)
        code = t.get("catalog_code")
        if not is_concentrated(count, limit_up_total):
            t["participants"] = []
            t["participants_note"] = (
                f"题材涨停 {count} 家，未达集中阈值"
                f"（≥{MIN_THEME_LIMIT_UPS} 家且占当日涨停 ≥{MIN_THEME_SHARE:.0%}）——不挖掘联动股"
            )
            continue
        if not code:
            t["participants"] = []
            t["participants_note"] = "该题材未挂靠到官方概念容器——无成分可挖"
            continue
        mined += 1
        stats: dict = {}
        audit_rows: list[dict] = []
        cands = linkage_candidates(
            container={"code": code, "name": t.get("theme")},
            member_symbols=members_by_code.get(code) or [],
            ever_sealed_symbols=ever_sealed_symbols,
            snapshot_by=snapshot_by,
            snapshot_state=snapshot_state,
            snapshot_as_of=snapshot_as_of,
            theme_limit_ups=count,
            theme_stage=t.get("stage"),
            per_theme=per_theme,
            stats=stats,
            audit_rows=audit_rows,
        )
        t["participants"] = cands
        # 只在归档前短暂携带，路由完成 point-in-time 写入后会移除，避免扩大公开响应。
        t["_candidate_audit"] = audit_rows
        total += len(cands)
        missing_quote += stats.get("missing_quote") or 0
        opened_after_seal += stats.get("opened_after_seal") or 0
        current_sealed += stats.get("current_sealed") or 0
        current_unknown += stats.get("current_unknown") or 0
        # 板块权限挡下的成分股数**逐题材留痕**：否则"这个题材 0 只候选"
        # 分不清是"没有联动迹象"还是"有，但都在没权限的板块"
        blocked += stats.get("excluded_board") or 0
        for label, n in (stats.get("excluded_board_labels") or {}).items():
            blocked_labels[label] = blocked_labels.get(label, 0) + n
        if cands:
            t["participants_note"] = None
        elif stats.get("members") and stats["missing_quote"] >= stats["members"]:
            # 全市场快照未覆盖该容器**任何**成分 ⇒ 判不了，不是"没有候选"。
            # 冷启动（后端刚起，首次抓取未完成）与数据源停更都会走到这里；
            # 不区分的话页面上两者同形，且会被 60s 装配缓存放大（2026-09-15 实测）。
            t["participants_note"] = (
                "全市场快照未覆盖本容器任何成分（冷启动抓取中 / 数据源停更）"
                "——本轮**不可判定**，不是「没有可参与标的」"
            )
        else:
            t["participants_note"] = (
                "容器内无可参与成分（未涨停 + 板块权限 + 涨幅/成交额/快照达标者均为 0）"
            )
    return {
        "themes_mined": mined,
        "candidates": total,
        # 账户权限（主板）挡下的只数：解释"候选为什么这么少"的第一手证据
        "excluded_board": blocked,
        "excluded_board_labels": dict(sorted(blocked_labels.items(), key=lambda kv: -kv[1])),
        # 快照未覆盖的成分总数：>0 且候选为 0 时说明"判不了"，不是"没有"
        "missing_quote": missing_quote,
        "opened_after_seal": opened_after_seal,
        "current_sealed": current_sealed,
        "current_unknown": current_unknown,
        "snapshot_state": snapshot_state or "unknown",
        "snapshot_as_of": snapshot_as_of,
    }


def top_watch_stocks(payload: dict, *, limit: int | None = None) -> dict:
    """盘中跟踪「最推荐标的」：**可参与**的题材联动候选（2026-09-15 用户口径）。

    **与旧口径的差别（必须写清楚，否则新旧两组胜率不可比）**：

    | | 旧（09-04 ~ 09-15） | 新（09-15 起） |
    |---|---|---|
    | `items` 来源 | 涨停梯队（曾封板身份） | 题材 `participants`（**当前未封板**） |
    | 判据 | certainty=封板质量（封单/首封/炸板） | linkage=题材基座 × 距封板跑道 |
    | 隐含可操作性 | 把历史封板误当 current | 仅表示可进入参与评估，不承诺成交 |
    | 涨停梯队 | 混在 `items` 里 | 移入 `reference_items`，显式标注仅参考 |

    机会度优先级（显式 if 链，可回测、可复盘对照）：

      1. linkage=高 —— 题材成建制（涨停 ≥3 家）且个股已进临板区、**当前未封板**
      2. linkage=中 —— 题材成建制且个股涨幅已过跟进线

    `低`/`unknown` 一律不入选（三态纪律：判不出不冒充机会，绝不拿「低」凑数）。
    排序：tier 升序，同 tier 内按当日涨幅降序——涨幅是"资金正在定价"的直接证据。

    :param limit: 展示容量上限。未传 → 取**运行时生效值**（控制台参数白名单
        可调 `picks_intraday_top_limit`，P1-15），否则回落 `TOP_WATCH_LIMIT`。
        这是**纯展示容量**：改它不动筛选逻辑，也不改「谁能入选」。
    """
    if limit is None:
        from app.core import runtime_params

        limit = runtime_params.get("picks_intraday_top_limit", TOP_WATCH_LIMIT)
    items: list[dict] = []
    reference: list[dict] = []
    participant_symbols = {
        str(s.get("symbol") or "")
        for t in payload.get("themes") or []
        for s in (t.get("participants") or [])
        if s.get("symbol")
    }
    for t in payload.get("themes") or []:
        for s in t.get("participants") or []:
            level = (s.get("linkage") or {}).get("level")
            if level == "高":
                tier, basis = 1, "联动确定性高：题材成建制且已进临板区（当前未封板、进入参与评估）"
            elif level == "中":
                tier, basis = 2, "联动确定性中：题材成建制且个股已见跟进迹象"
            else:
                continue
            items.append(
                {
                    "symbol": s.get("symbol"),
                    "name": s.get("name"),
                    # participant 的 current 状态不等于梯队角色；不臆造"跟风/补涨"这类封板语义标签
                    "role": None,
                    "boards": None,
                    "change_pct": s.get("change_pct"),
                    "theme": t.get("theme"),
                    "stage": t.get("stage"),
                    "strength_tier": t.get("strength_tier"),
                    "reason": None,  # 涨停原因只有涨停股才有（同花顺口径）
                    # 辨识度/确定性是"封板质量"维度的判定，对未封板个股不适用——
                    # 传 None 让前端显示"不适用"，而不是硬套一套封板判据
                    "distinctiveness": None,
                    "certainty": None,
                    "linkage": s.get("linkage"),
                    "tradability": s.get("tradability"),
                    "seal_state": s.get("seal_state"),
                    "board": s.get("board"),
                    "tier": tier,
                    "pick_basis": f"{basis}；{s.get('basis') or ''}".rstrip("；"),
                }
            )
        # 涨停梯队 → 参考信息（用户指令：开盘即涨停的个股「只能作为参考信息」）
        for s in t.get("stocks") or []:
            reference.append(
                {
                    "symbol": s.get("symbol"),
                    "name": s.get("name"),
                    "role": s.get("role"),
                    "boards": s.get("boards"),
                    "change_pct": s.get("change_pct"),
                    "theme": t.get("theme"),
                    "stage": t.get("stage"),
                    "strength_tier": t.get("strength_tier"),
                    "reason": s.get("reason"),
                    "first_seal_time": s.get("first_seal_time"),
                    "board": s.get("board"),
                    # 板块权限事实随行带上——下面的兜底筛选读的就是它（生产路径里
                    # 路由已先物理剔除，这里是"函数独立可用"的保证）
                    "tradable": s.get("tradable"),
                    "distinctiveness": s.get("distinctiveness"),
                    "certainty": s.get("certainty"),
                    # 参考区保留“今日曾封板”历史身份；current 状态由 tradability/seal_state 随行说明
                    "tradability": s.get("tradability"),
                    "seal_state": s.get("seal_state"),
                    "reference_only": True,
                }
            )
    board_excluded = int(payload.get("board_excluded_reference") or 0)
    items.sort(key=lambda x: (x["tier"], -(x["change_pct"] or 0.0)))
    # 兜底再筛一次：即使调用方漏做了板块拆分，参考区也不会混进买不了的票。
    # 用的仍是生产者写下的**同一个事实**（`tradable`），不是第二个判据实现。
    reference = [
        r for r in reference
        if r.get("tradable") is not False and str(r.get("symbol") or "") not in participant_symbols
    ]
    reference.sort(key=lambda x: -(x.get("boards") or 0))
    closed = sum(1 for r in reference if (r.get("tradability") or {}).get("level") == "不可参与")
    unknown = sum(1 for r in reference if (r.get("tradability") or {}).get("level") in (None, "unknown"))
    return {
        "trade_date": payload.get("trade_date"),
        "items": items[:limit],
        "reference_items": reference[:limit],
        "total_candidates": len(items),
        "reference_total": len(reference),
        "criteria": (
            "机会度＝联动确定性（题材成建制 × 距封板跑道）优先、当日涨幅次之；"
            "候选须同时满足「当前未封板」与「账户有交易权限（当前仅沪深主板）」；"
            "可参与仅表示进入评估，不保证盘口成交；低/unknown 不入选"
        ),
        "reference_criteria": (
            f"涨停梯队历史参考 {len(reference)} 只（当前不可参与 {closed} 只、当前状态未判 {unknown} 只）"
            f"——仅作题材集中度的参考信息；已开板并进入 participant 的股票已从参考区去重"
            + (
                f"；另有 {board_excluded} 只属非主板板块（账户无交易权限）已不在本页展示"
                if board_excluded
                else ""
            )
        ),
        "board_excluded_reference": board_excluded,
        "tradable_boards": "沪市主板 / 深市主板（含主板 ST）",
        "hot_available": payload.get("hot_available"),
        "caveats": list(payload.get("caveats") or []),
    }


def attach_risk_fields(stocks: list[dict], snap_by: dict[str, dict]) -> None:
    """就地补 现价 / 止损参考 / 出场纪律（与每日精选 PickCard 同构的三项，纯函数）。

    为什么抽成共用函数：这三项 2026-09-09 起只在 ``/api/picks/intraday-top`` 的
    路由里补，而题材手风琴走的是 ``/api/picks/intraday-opportunities`` ⇒ **同一张
    选股卡片（PickCard）被两套字段丰度喂**，手风琴展开后缺现价/止损/出场纪律，
    看起来像「字段没对齐」（2026-09-10 用户反馈）。补全逻辑只允许有一份。

    三态纪律：现价取全市场快照，取不到写 ``None``（显式缺失，由前端渲染 ``--``），
    不拿昨收或 0 冒充；``stop_loss_reference`` 在 price 为 None 时自行降级。
    """
    from app.picks.risk import exit_discipline, risk_tier_of, stop_loss_reference

    for s in stocks:
        price = (snap_by.get(str(s.get("symbol") or "")) or {}).get("price")
        s["price"] = price
        tier = risk_tier_of(s.get("role") or "")
        s["stop_ref"] = stop_loss_reference(price=price, tier=tier)
        s["exit_plan"] = exit_discipline(tier)
