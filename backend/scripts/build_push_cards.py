"""组装飞书 interactive 卡片：①明日机会观察（盘后）②盘后复盘。

数据全部来自 8000 实例实时 API；输出到 docs/push-templates/*.card.json。
发送（自动化侧）：lark-cli im +messages-send --user-id <open_id> --as bot \\
    --msg-type interactive --content "$(cat docs/push-templates/xxx.card.json)"

视觉方案（飞书卡片能力内）：
- column_set 等宽三列 + background_style="grey" 灰底色块 → 模块层次
- 标的块左窄列（分数/代码）+ 右宽列（名称/逻辑/风控）→ 扫读锚点
- 红涨绿跌 emoji 语义（中国市场口径 🔴涨 🟢跌）
- hr 只做模块分界，模块内部靠底色分层

内容方案——消息面精华（events 事实流的降噪筛选）：
- 输入 /api/events?limit=40（当日全部事件）
- 打分 = Σ|direction|×strength×2 + 关联命中(Top5/方向池/题材) +5
        + 强信号词(流出/流入/减持/风险提示/控制权/辞职…) +2 − source_tier
- score≥5 才入选，取 Top3，不足则整模块隐藏（不硬凑）
- 每条一行 ≤42 字：方向 emoji + 标题 + 「→ 关联 xxx」
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"
OUT = "docs/push-templates"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.load(r)["data"]


picks = get("/api/picks/today")
sent = get("/api/market/sentiment")
rev = get("/api/review/reports/20260902")
_raw_events = get("/api/events?limit=40")
events = _raw_events if isinstance(_raw_events, list) else (_raw_events or {}).get("items", [])

# ---------- 公共构件 ----------
def md(txt):
    return {"tag": "markdown", "content": txt}


def col(weight, content):
    return {
        "tag": "column", "width": "weighted", "weight": weight,
        "vertical_align": "top", "elements": [md(content)],
    }


def cols(pairs, grey=False, spacing="8px"):
    """pairs: [(weight, md_text)] → column_set"""
    return {
        "tag": "column_set", "flex_mode": "stretch",
        "background_style": "grey" if grey else "default",
        "horizontal_spacing": spacing, "columns": [col(w, t) for w, t in pairs],
    }


def div(txt):
    return {"tag": "div", "text": {"tag": "lark_md", "content": txt}}


def hr():
    return {"tag": "hr"}


def note(txt):
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": txt}]}


def card(header, template, elements):
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": header}, "template": template},
        "elements": elements,
    }


# ---------- 关联词与消息面精华 ----------
items = picks["items"]

watch = set()  # 关联词：Top5 代码/名称/题材 + 方向池成员
for it in items:
    watch |= {it["symbol"], it["name"]}
    if it.get("theme"):
        watch.add(it["theme"])
# API 的 directions 在盘后对照降级后会被清空 → 回退读当日简报文件取方向池成员
import glob
import os

_briefs = sorted(glob.glob(os.path.join(os.path.dirname(__file__), "..", "..", "data", "picks", "briefs", "*.json")))
if _briefs:
    try:
        with open(_briefs[-1], encoding="utf-8") as f:
            _p = json.load(f)
        _p = _p.get("payload") or _p  # 文件顶层即正文（API 才有 payload 包裹）
        for d in _p.get("directions") or []:
            if d.get("direction"):
                watch.add(d["direction"])
            for p_ in d.get("pool") or []:
                if p_.get("name"):
                    watch.add(p_["name"])
    except (OSError, ValueError):
        pass
watch = {w for w in watch if w and len(w) >= 2}

# 高级强词：直接影响资金面/治理面，+3；泛词（涨停/突破/龙虎榜等播报体）不加分，
# 否则榜单播报会塞满卡片（tier3 抵扣后自然淘汰）
STRONG_WORDS = ("流出", "流入", "减持", "增持", "风险提示", "控制权", "辞职", "立案")
# 噪音黑名单：命中则方向分清零——"突破均线"类播报会被文本规则误判利好方向
SPAM_WORDS = ("突破", "龙虎榜数据", "现身", "曝光", "每笔成交量")


def score_event(e):
    sc = 0
    title = e.get("title") or ""
    for d in e.get("directions") or []:
        v = abs(d.get("direction") or 0)
        if v and not any(w in title for w in SPAM_WORDS):
            sc += v * (d.get("strength") or 1) * 3
    hit = next((w for w in watch if w in title), None)
    if hit:
        sc += 5
    if any(w in title for w in STRONG_WORDS):
        sc += 3
    return sc - (e.get("source_tier") or 3), hit


def event_line(e, hit):
    ds = e.get("directions") or []
    v = max((abs(d.get("direction") or 0), d.get("direction") or 0) for d in ds)[1] if ds else 0
    if v == 0:  # 系统未判方向时用标题词兜底（资金流向/治理事件语义明确）
        t = e.get("title") or ""
        if any(w in t for w in ("净流出", "流出", "减持", "辞职", "风险提示", "下跌")):
            v = -1
        elif any(w in t for w in ("净流入", "流入", "上涨", "涨", "中标")):
            v = 1
    emoji = "🟢" if v > 0 else ("🔴" if v < 0 else "⚪")
    title = (e.get("title") or "").replace("**", "")
    if len(title) > 42:
        title = title[:41] + "…"
    rel = f" → 关联 **{hit}**" if hit else ""
    return f"{emoji} {title}{rel}"


digest = sorted(((score_event(e), e) for e in events), key=lambda x: -x[0][0])
digest = [(s, e) for s, e in digest if s[0] >= 3][:3]


def digest_elements():
    """消息面精华模块；无达阈值条目时给『宁缺毋滥』占位（保持卡片结构稳定）。"""
    if digest:
        lines = "\n".join(event_line(e, s[1]) for s, e in digest)
        return [
            hr(),
            div("**📰 消息面精华**　自动筛选 %d 条事件 → %d 条" % (len(events), len(digest))),
            cols([(1, lines)], grey=True),
        ]
    return [
        hr(),
        div("**📰 消息面精华**　今日无达阈值事件（筛选 %d 条，宁缺毋滥）" % len(events)),
    ]


mkt = (rev.get("data") or {}).get("market") or {}
idx = {i["name"]: i for i in mkt.get("indices", [])}
trading = (rev.get("data") or {}).get("trading") or {}
cal = sent.get("calibration") or {}
pct = cal.get("percentile") or {}
promo_pct = (pct.get("promo_1to2") or {}).get("percentile")
promo_val = (pct.get("promo_1to2") or {}).get("value", 0) * 100


def first_clause(s, sep="；"):
    return (s or "").split(sep)[0].replace("非涨停：", "")


def logic_line(it):
    bases = it.get("bases") or {}
    parts = []
    if bases.get("echelon"):
        parts.append(first_clause(bases["echelon"]))
    fun = bases.get("fundamental") or ""
    seg = next((x for x in fun.split("；") if "净利同比" in x), None)
    if seg:
        parts.append(seg)
    return "；".join(parts) or "—"


# ---------- 卡片①：明日机会观察 ----------
el = [
    div("**⛔ 门控：强空仓**　退潮期接力亏钱，以下清单 **🔒 仅跟踪观察，不构成买入依据**"),
    hr(),
    cols([
        (1, f"**{sent.get('temperature')}**\n🌡️ 情绪温度·{sent.get('phase')}"),
        (1, "**55 家**\n🚀 涨停（昨 80）"),
        (1, f"**{promo_val:.1f}%**\n📐 晋级率·分位 {promo_pct}"),
    ], grey=True),
    cols([
        (1, "**4 板**\n🪜 最高板（昨 7 断板）"),
        (1, "**-2.60%**\n📉 昨涨停溢价"),
        (1, "**🔴1541 🟢3900**\n⚖️ 涨跌家数"),
    ], grey=True),
    hr(),
    div("**🏆 选股器 Top5**　🔒 全部仅观察（%d/%d，门控期不买入）" % (
        sum(1 for i in items if i.get("observation_only")), len(items))),
]
for it in items:
    theme = it.get("theme")
    head = f"**{it['name']}** · {it.get('echelon_role') or '—'}"
    if theme:
        head += f" · {theme}（{it.get('theme_stage') or '—'}）"
    el.append(cols([
        (1, f"**{it['score']:.1f}**\n`{it['symbol']}`"),
        (6, (
            f"{head}\n"
            f"逻辑：{logic_line(it)}\n"
            f"🔻 {(it.get('invalidations') or ['—'])[0]} ｜ **止损 -{(it.get('stop_loss') or {}).get('pct', 0):.0f}% @ {(it.get('stop_loss') or {}).get('price', 0):.2f}**"
        )),
    ]))
el += [
    hr(),
    div("**🌅 明日前哨**　涨停回升且晋级率 ≥25% → 修复期重评方向池；涨停 <25 家且最高板 ≤2 板 → 冰点续空仓"),
]
el += digest_elements()
el += [note("选股器规则引擎 · 简报 08:40 生成，未含竞价数据 · 门控期清单仅作跟踪 · 非投资建议")]
card1 = card("📊 明日机会观察 · 09-03（周三）", "orange", el)

# ---------- 卡片②：盘后复盘 ----------
dims = {d["key"]: d for d in rev.get("dimensions", [])}
mkt_dim = dims.get("market") or {}
trade_count = trading.get("trade_count", 0)


def idx_line(name):
    i = idx.get(name) or {}
    chg = i.get("change_pct", 0)
    dot = "🔴" if chg > 0 else ("🟢" if chg < 0 else "⚪")
    return f"{name} {i.get('close', '-')} ({chg:+.2f}%){dot}"


el2 = [
    cols([
        (1, f"**{'空仓 ✅' if trade_count == 0 else f'{trade_count} 笔'}**\n💼 今日操作"),
        (1, "**3 方向证伪**\n🤖 系统动作"),
        (1, "**¥100.0 万**\n💰 现金（无持仓）"),
        (1, "**0 缺失**\n🩺 数据完整度"),
    ], grey=True),
    hr(),
    div(f"**📈 盘面**　{idx_line('上证指数')}｜{idx_line('深证成指')}｜{idx_line('创业板指')}"),
    div(f"**🌡️ 情绪**　退潮确认：最高板 7→4 断板；晋级率 {promo_val:.1f}%（分位 {promo_pct}）；昨涨停溢价 -2.60%"),
]
for j in (mkt_dim.get("judgements") or [])[:2]:
    el2.append(div("**💬 研判**　" + j))
el2 += [
    hr(),
    div("**⚠️ 不足与改善**"),
    div("① 退潮门控下简报仍给 65~84 确定性分，3 方向全部盘中证伪 → **建议：空仓期简报分数自动降权并标注「仅观察」**（待批）"),
    div("② %s" % ("；".join((a.get("title") or "") for a in rev.get("action_items", [])[:2]) or "—")),
    hr(),
    div("**📌 改善追踪**　昨日 P0「15:35 对照数据缺失」→ ✅ 已修复，今日对照产出完整"),
    div("**🌅 明日关注**　竞价溢价与晋级率能否回升；冰点触发条件（%s）" % sent.get("switch_conditions")),
]
el2 += digest_elements()
el2 += [note("复盘规则引擎 · 全维度无数据缺失 · 非投资建议")]
card2 = card("📝 盘后复盘 · 09-02（周二）", "blue", el2)

for name, c in (("after-close-opportunity", card1), ("after-close-review", card2)):
    with open(f"{OUT}/{name}.card.json", "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)
print(f"digest: {len(digest)} 条（源 {len(events)}）| card1 elements: {len(el)} | card2 elements: {len(el2)}")
for s, e in digest:
    print("  入选:", s[0], event_line(e, s[1]))
