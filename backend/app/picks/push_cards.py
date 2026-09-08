"""飞书推送卡片构建（v2 版式单点）——脚本 build_push_cards.py 与服务端共用。

版式定稿（2026-09-02 用户确认，docs/push-templates 历史约定）：
- 指标区用飞书原生 fields 双列栅格（is_short），标题粗体一行 + 数值一行
- 个股块纯 markdown 多行：名称/分数/止损加粗，逻辑、失效、止损各占一行
- hr 只做模块分界

2026-09-08 从 backend/scripts/build_push_cards.py 抽取为可 import 模块：
盘中买点推送（app/picks/buy_point.py）要求卡片「布局与内容结构与每日精选
卡片完全一致」——唯一可靠途径是同一份构建函数。本模块**纯函数零 IO**，
sent/breadth/picks 全部由调用方传入。

⚠️ 消息面/新闻模块已按用户 2026-09-02 安排移除：推送不含任何新闻内容。
"""
from __future__ import annotations

WEEKDAY = "一二三四五六日"


def ind(sent: dict | None, name: str) -> float | int | None:
    """情绪指标取值（name → value），缺失返回 None，绝不冒充 0。"""
    for i in (sent or {}).get("indicators") or []:
        if i.get("name") == name:
            return i.get("value")
    return None


def fmt_chg(v: float | None) -> str:
    """涨跌幅格式化：None → '--'（三态纪律：缺数据不冒充 0）。"""
    return f"{v:+.2f}%" if v is not None else "--"


# ---------- 公共构件（v2 版式） ----------
def field(title: str, body: str) -> dict:
    return {"is_short": True, "text": {"tag": "lark_md", "content": f"**{title}**\n{body}"}}


def fields_grid(pairs: list[tuple[str, str]]) -> dict:
    return {"tag": "div", "fields": [field(t, b) for t, b in pairs]}


def hr() -> dict:
    return {"tag": "hr"}


def div(md_text: str) -> dict:
    return {"tag": "div", "text": {"tag": "lark_md", "content": md_text}}


def note(txt: str) -> dict:
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": txt}]}


def card(header: str, template: str, elements: list[dict]) -> dict:
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": header}, "template": template},
        "elements": elements,
    }


def first_clause(s: str | None, sep: str = "；") -> str:
    return (s or "").split(sep)[0].replace("非涨停：", "")


def logic_line(it: dict) -> str:
    bases = it.get("bases") or {}
    parts = []
    if bases.get("echelon"):
        parts.append(first_clause(bases["echelon"]))
    fun = bases.get("fundamental") or ""
    seg = next((x for x in fun.split("；") if "净利同比" in x), None)
    if seg:
        parts.append(seg)
    return "；".join(parts) or "—"


def sentiment_pairs(sent: dict | None, breadth: dict | None) -> list[tuple[str, str]]:
    """情绪指标双列栅格（各卡共用），缺失显式 '--'。"""
    lim_up = ind(sent, "涨停家数")
    lim_conn = ind(sent, "连板家数")
    max_boards = ind(sent, "连板高度")
    yesterday_mid = ind(sent, "昨日涨停今日中位")
    cal = (sent or {}).get("calibration") or {}
    pct = cal.get("percentile") or {}
    promo_pct = (pct.get("promo_1to2") or {}).get("percentile")
    promo_val = (pct.get("promo_1to2") or {}).get("value", 0) * 100
    return [
        ("🌡️ 情绪温度", f"{(sent or {}).get('temperature')} · {(sent or {}).get('phase')}"),
        ("🚀 涨停 / 连板", f"{lim_up if lim_up is not None else '--'} 家 / {lim_conn if lim_conn is not None else '--'} 家连板"),
        ("📐 首板晋级率", f"{promo_val:.1f}%（分位 {promo_pct}）"),
        ("📉 昨涨停溢价", f"{yesterday_mid:+.2f}%（中位）" if yesterday_mid is not None else "--"),
        ("🔎 涨跌家数", f"涨 {(breadth or {}).get('up', '--')} / 跌 {(breadth or {}).get('down', '--')}"),
        ("🪜 最高板", f"{max_boards}" if max_boards is not None else "--"),
    ]


def gate_banner(gate: dict | None) -> tuple[str, str, bool]:
    """门控横幅接真实 gate 判定（曾经硬编码"强空仓"，会与实际 gate 状态不符）。

    返回 (gate_head, gate_body, gate_stand)；gate_stand 决定 TopN 头行的仅观察标注。
    """
    gate_level = (gate or {}).get("level")
    gate_stand = bool((gate or {}).get("stand_aside"))
    if gate_stand:
        return ("**⛔ 门控：强空仓**" if gate_level == "strong" else "**⛔ 门控：空仓观察**",
                "门控条件触发，以下清单 **🔒 仅跟踪观察，不构成买入依据**", gate_stand)
    return "**✅ 门控：正常**", "未触发空仓闸门；各标的执行状态以盘前竞价闸门为准", gate_stand


# ---------- 卡片：每日精选（morning=当日跟踪清单；默认=次日前瞻） ----------
def build_picks_card(picks: dict, sent: dict | None, breadth: dict | None, exec_gate: dict | None,
                     *, show, title_prefix: str, with_exec: bool) -> dict:
    items = picks["items"]
    gate = (picks.get("meta") or {}).get("gate")
    gate_head, gate_body, gate_stand = gate_banner(gate)
    observe_n = sum(1 for i in items if i.get("observation_only"))

    exec_by_sym: dict[str, dict] = {}
    if with_exec and exec_gate:
        exec_by_sym = {i["symbol"]: i for i in exec_gate.get("items") or []}
    STATE_TAG = {"blocked": "🚫 禁买", "observe": "⚠️ 观察", "normal": "✅ 可买",
                 "anomaly": "❗ 异常", "unknown": "❓ 未知"}

    el = [
        div(f"{gate_head}　{gate_body}"),
        hr(),
        fields_grid(sentiment_pairs(sent, breadth)),
        hr(),
        div((f"**🏆 选股器 Top{len(items)}**　🔒 全部仅观察（{observe_n}/{len(items)}，门控期不买入）" if gate_stand
             else f"**🏆 选股器 Top{len(items)}**　仅观察 {observe_n}/{len(items)}")),
    ]
    for n, it in enumerate(items, 1):
        theme = it.get("theme")
        head = f"**{n}｜{it['name']} {it['symbol']}**　**{it['score']:.1f} 分** · {it.get('echelon_role') or '—'}"
        if theme:
            head += f" · {theme}（{it.get('theme_stage') or '—'}）"
        sl = it.get("stop_loss") or {}
        exec_line = ""
        if with_exec:
            exec_row = exec_by_sym.get(it["symbol"])
            exec_tag = STATE_TAG.get((exec_row or {}).get("state"), "❓ 未知")
            reason = (exec_row or {}).get("reason") or "执行闸门无数据"
            exec_line = f"执行：**{exec_tag}**　{reason}\n"
        el.append(div(
            f"{head}\n"
            f"逻辑：{logic_line(it)}\n"
            f"失效：{(it.get('invalidations') or ['—'])[0]}\n"
            f"止损：**-{sl.get('pct', 0):.0f}% @ {sl.get('price', 0):.2f}**\n"
            f"{exec_line}".rstrip()
        ))
    el += [
        hr(),
        div("**🌅 明日前哨**　涨停回升且晋级率 ≥25% → 修复期重评方向池；涨停 <25 家且最高板 ≤2 板 → 冰点续空仓\n"
            f"当前切换条件：{(sent or {}).get('switch_conditions')}"),
        note("选股器规则引擎 · 简报 08:40 生成 · 名单为盘中跟踪输入，机会确认以盘中提醒为准 · 非投资建议"
             if with_exec else
             "选股器规则引擎 · 次日名单 08:40 生成，本卡为基于今日盘面的前瞻 · 非投资建议"),
    ]
    return card(f"📈 {title_prefix} · {show:%m-%d}（周{WEEKDAY[show.weekday()]}）", "orange", el)


# ---------- 卡片：盘中买点（2026-09-08 用户定稿：唯一保留的盘中飞书推送） ----------
def _buy_point_gate_banner(gate: dict | None) -> tuple[str, str]:
    """买点卡门控行：闸门期文案强调「可跟 ≠ 可买」纪律（P1-2 定稿）。"""
    gate_stand = bool((gate or {}).get("stand_aside"))
    if gate_stand:
        return ("**⛔ 门控：强空仓**" if (gate or {}).get("level") == "strong" else "**⛔ 门控：空仓观察**",
                "闸门期不买入；以下为满足可跟判据的标的（**不给买入范围**，参与须经影子持仓先验证）")
    return "**✅ 门控：正常**", "未触发空仓闸门；买点以买入区间内现价为准"


def build_buy_point_card(hits: list[dict], sent: dict | None, breadth: dict | None,
                         gate: dict | None, *, show) -> dict:
    """盘中买点卡：与 build_picks_card 同版式（gate 行/情绪栅格/个股块/note）。

    hits 元素：{"item": 当日精选 item dict, "price": 现价, "chg": 涨幅|None,
    "tier_label": 置信档中文, "low": 区间下沿, "high": 区间上沿}。
    个股块头部/逻辑/失效/止损行与每日精选卡**逐字同构**，额外追加一行
    「判定」挂多因素证据（置信档/区间/涨幅）——可解释性红线。
    """
    gate_head, gate_body = _buy_point_gate_banner(gate)
    el = [
        div(f"{gate_head}　{gate_body}"),
        hr(),
        fields_grid(sentiment_pairs(sent, breadth)),
        hr(),
        div(f"**🎯 盘中买点命中 {len(hits)} 只**　多因素判定：置信档 ≥ 可执行 · 无红线否决 · 现价入买入区间 · 未触涨停区"),
    ]
    for n, h in enumerate(hits, 1):
        it = h["item"]
        theme = it.get("theme")
        head = f"**{n}｜{it['name']} {it['symbol']}**　**{it['score']:.1f} 分** · {it.get('echelon_role') or '—'}"
        if theme:
            head += f" · {theme}（{it.get('theme_stage') or '—'}）"
        sl = it.get("stop_loss") or {}
        chg = h.get("chg")
        chg_txt = f"{chg:+.2f}%" if chg is not None else "--"
        el.append(div(
            f"{head}\n"
            f"逻辑：{logic_line(it)}\n"
            f"失效：{(it.get('invalidations') or ['—'])[0]}\n"
            f"止损：**-{sl.get('pct', 0):.0f}% @ {sl.get('price', 0):.2f}**\n"
            f"判定：**{h['tier_label']}** · 现价 {h['price']:.2f} ∈ 买入区间 {h['low']:.2f}-{h['high']:.2f} · 涨幅 {chg_txt}"
        ))
    el += [
        hr(),
        note("盘中买点规则引擎 · 当日精选多因素判定可上车时推送 · 每票每日至多一推 · 非投资建议"),
    ]
    return card(f"📈 盘中买点 · {show:%m-%d}（周{WEEKDAY[show.weekday()]}）", "orange", el)
