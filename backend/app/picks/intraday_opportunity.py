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

TODO（诚实口径）：阈值（人气榜名次边界、封单额下限）是经验初值，未经回测校准；
调参时以 basis 文案里的证据回放历史，统计高确定性组的 T+1 胜率后定稿。
"""
from __future__ import annotations

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
        reason_by_symbol = {
            c["symbol"]: c.get("reason") for c in ((t.get("leaders") or {}).get("candidates") or [])
        }
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
                    "reason": reason_by_symbol.get(sym),
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


def top_watch_stocks(payload: dict, *, limit: int = TOP_WATCH_LIMIT) -> dict:
    """盘中跟踪「最推荐标的」筛选（2026-09-04 用户需求，工作台动态分组 + 复盘共用口径）。

    多维评估的显式 if 链（可回测、可复盘对照），机会度优先级：
      1. certainty=高 且 distinctiveness=高 —— 题材核心 + 高辨识，第一梯队
      2. certainty=高 —— 延续预期最硬（确定性优先：「机会最大」首先看能不能延续）
      3. certainty=中 且 distinctiveness=高 —— 市场焦点股，题材延续待验证
    unknown / 低一律不入选（三态纪律：判不出不冒充机会，绝不拿「低」凑数）。

    顺序：tier 升序稳定排序——同 tier 内保持 opportunities 的题材强度序与
    梯队连板序（sort 稳定性保证，不引入额外权重以免丢失可解释性）。
    输出是机会度排序，不是买卖建议（红线）；依据文案逐只可追溯。
    """
    items: list[dict] = []
    for t in payload.get("themes") or []:
        for s in t.get("stocks") or []:
            cert = (s.get("certainty") or {}).get("level")
            dist = (s.get("distinctiveness") or {}).get("level")
            if cert == "高" and dist == "高":
                tier, basis = 1, "确定性高＋辨识度高：题材核心且延续预期硬"
            elif cert == "高":
                tier, basis = 2, "确定性高：题材阶段与封板质量支持延续（辨识度未到高）"
            elif cert == "中" and dist == "高":
                tier, basis = 3, "确定性中＋辨识度高：市场焦点股，延续预期待验证"
            else:
                continue
            items.append(
                {
                    "symbol": s.get("symbol"),
                    "name": s.get("name"),
                    "role": s.get("role"),
                    "boards": s.get("boards"),
                    "change_pct": s.get("change_pct"),
                    "theme": t.get("theme"),
                    "stage": t.get("stage"),
                    "strength_tier": t.get("strength_tier"),
                    "distinctiveness": s.get("distinctiveness"),
                    "certainty": s.get("certainty"),
                    "reason": s.get("reason"),
                    "tier": tier,
                    "pick_basis": basis,
                }
            )
    items.sort(key=lambda x: x["tier"])
    return {
        "trade_date": payload.get("trade_date"),
        "items": items[:limit],
        "total_candidates": len(items),
        "criteria": "机会度＝确定性(题材阶段×封板质量)优先、辨识度(人气×高度×角色)次之；unknown/低不入选",
        "hot_available": payload.get("hot_available"),
        "caveats": list(payload.get("caveats") or []),
    }
