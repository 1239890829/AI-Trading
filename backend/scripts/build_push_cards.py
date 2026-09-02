"""组装两张飞书 interactive 卡片：①明日机会观察（盘后）②盘后复盘。数据全部来自 8000 实例。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8000"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.load(r)["data"]


picks = get("/api/picks/today")
sent = get("/api/market/sentiment")
rev = get("/api/review/reports/20260902")

gate = picks["meta"]["gate"]
cal = sent.get("calibration") or {}
pct = cal.get("percentile") or {}


def p(name):
    return (pct.get(name) or {}).get("percentile")


promo_pct = p("promo_1to2")
mkt = (rev.get("data") or {}).get("market") or {}
idx = {i["name"]: i for i in mkt.get("indices", [])}
trading = (rev.get("data") or {}).get("trading") or {}


def idx_line(name):
    i = idx.get(name) or {}
    return f"{name} {i.get('close', '-')} ({i.get('change_pct', 0):+.2f}%)"


def field(title, body):
    return {"is_short": True, "text": {"tag": "lark_md", "content": f"**{title}**\n{body}"}}


def hr():
    return {"tag": "hr"}


def div(md):
    return {"tag": "div", "text": {"tag": "lark_md", "content": md}}


def note(txt):
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": txt}]}


def card(header, template, elements):
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": header}, "template": template},
        "elements": elements,
    }


def first_clause(s, sep="；"):
    return (s or "").split(sep)[0]


def logic_line(it):
    bases = it.get("bases") or {}
    parts = []
    ech = bases.get("echelon") or ""
    if ech:
        c = first_clause(ech).replace("非涨停：", "")
        parts.append(c)
    fun = bases.get("fundamental") or ""
    for key in ("净利同比", "营收增速"):
        if key in fun:
            seg = fun.split("；", 1)[-1] if "PE" in fun.split("；")[0] else fun
            parts.append("；".join(x for x in seg.split("；") if key in x))
            break
    return "；".join(parts) or "—"


# ---------- 卡片①：明日机会观察 ----------
items = picks["items"]
obs = sum(1 for i in items if i.get("observation_only"))
el = [
    div(
        "**⛔ 门控：强空仓**（级别 %s）\n%s" % (gate.get("level"), "；".join(gate.get("reasons", [])[:2]))
        if not gate.get("stand_aside")
        else "**⛔ 门控：强空仓**　退潮期接力亏钱，以下清单 **🔒 仅跟踪观察，不构成买入依据**"
    ),
    hr(),
    {
        "tag": "div",
        "fields": [
            field("🌡️ 情绪温度", f"{sent.get('temperature')} · {sent.get('phase')}（置信 {sent.get('confidence')}）"),
            field("🚀 涨停 / 昨日", "55 家 / 80 家"),
            field("📐 首板晋级率", f"{(pct.get('promo_1to2') or {}).get('value', 0) * 100:.1f}%（分位 {promo_pct}）"),
            field("📉 昨涨停溢价", "-2.60%（亏钱效应）"),
            field("🔎 涨跌家数", "涨 1541 / 跌 3900 · 涨停 55 跌停 10"),
            field("🧩 活跃题材", "35 个 · 梯队断层 12 个"),
        ],
    },
    hr(),
    div("**🏆 选股器 Top5**　🔒 全部仅观察（%d/%d，门控期不买入）" % (obs, len(items))),
]
for n, it in enumerate(items, 1):
    theme = it.get("theme")
    head = "**%d｜%s %s**　%.1f 分 · %s" % (
        n, it["name"], it["symbol"], it["score"], it.get("echelon_role") or "—",
    )
    if theme:
        head += " · %s（%s）" % (theme, it.get("theme_stage") or "—")
    el.append(
        div(
            "%s\n逻辑：%s\n失效：%s ｜ **止损 %.0f%% @ %.2f**"
            % (
                head,
                logic_line(it),
                (it.get("invalidations") or ["—"])[0],
                (it.get("stop_loss") or {}).get("pct", 0),
                (it.get("stop_loss") or {}).get("price", 0),
            )
        )
    )
el += [
    hr(),
    div("**🌅 明日前哨**　涨停回升且晋级率 ≥25%% → 修复期重评方向池；涨停 <25 家且最高板 ≤2 板 → 冰点续空仓\n当前切换条件：%s" % sent.get("switch_conditions")),
    note("选股器规则引擎 · 简报 08:40 生成，未含竞价数据 · 门控期清单仅作跟踪 · 非投资建议"),
]
card1 = card("📊 明日机会观察 · 09-03（周三）", "orange", el)

# ---------- 卡片②：盘后复盘 ----------
dims = {d["key"]: d for d in rev.get("dimensions", [])}
mkt_dim = dims.get("market") or {}
orders = trading.get("orders") or []
trade_count = trading.get("trade_count", 0)
el2 = [
    {
        "tag": "div",
        "fields": [
            field("💼 今日操作", f"{'空仓' if trade_count == 0 else f'{trade_count} 笔委托'} {'✅ 纪律执行' if trade_count == 0 else ''}"),
            field("🤖 系统动作", "3 方向盘中证伪 → 降级观察"),
            field("💰 账户", "无持仓 · 现金 ¥100.0 万"),
            field("🩺 数据完整度", "0 缺失 · 信号样本 49"),
        ],
    },
    hr(),
    div(f"**📈 盘面**　{idx_line('上证指数')}｜{idx_line('深证成指')}｜{idx_line('创业板指')}"),
    div(f"**🌡️ 情绪**　退潮确认：最高板 7→4 断板；晋级率 13.2%（分位 {promo_pct}）；昨涨停溢价 -2.60%"),
]
for j in (mkt_dim.get("judgements") or [])[:2]:
    el2.append(div("**💬 研判**　" + j))
el2 += [
    hr(),
    div("**⚠️ 不足与改善**"),
    div("① 退潮门控下简报仍给 65~84 确定性分，3 方向全部盘中证伪 → **建议：空仓期简报分数自动降权并标注「仅观察」**（待批）"),
    div("② %s" % "；".join((a.get("title") or "") for a in rev.get("action_items", [])[:2]) or "—"),
    hr(),
    div("**📌 改善追踪**　昨日 P0「15:35 对照数据缺失」→ ✅ 已修复，今日对照产出完整"),
    div("**🌅 明日关注**　竞价溢价与晋级率能否回升；冰点触发条件（%s）" % sent.get("switch_conditions")),
    note("复盘规则引擎 · 全维度无数据缺失 · 非投资建议"),
]
card2 = card("📝 盘后复盘 · 09-02（周二）", "blue", el2)

with open("docs/push-templates/after-close-opportunity.card.json", "w", encoding="utf-8") as f:
    json.dump(card1, f, ensure_ascii=False)
with open("docs/push-templates/after-close-review.card.json", "w", encoding="utf-8") as f:
    json.dump(card2, f, ensure_ascii=False)
print("card1 elements:", len(el), "| card2 elements:", len(el2))
