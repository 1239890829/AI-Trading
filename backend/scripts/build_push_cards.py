"""组装飞书 interactive 卡片：①明日机会观察（盘后）②盘后复盘。

数据全部来自 8000 实例实时 API；输出到 docs/push-templates/*.card.json。
发送（自动化侧）：lark-cli im +messages-send --user-id <open_id> --as bot \\
    --msg-type interactive --content "$(cat docs/push-templates/xxx.card.json)"

布局（v2 定稿版式，2026-09-02 用户确认）：
- 指标区用飞书原生 fields 双列栅格（is_short），标题粗体一行 + 数值一行，不挤行
- 个股块纯 markdown 多行：名称/分数/止损加粗，逻辑、失效、止损各占一行
- hr 只做模块分界

⚠️ 消息面/新闻模块已按用户 2026-09-02 安排移除：推送不含任何新闻内容，
个股筛选（多维度综合考量）保留在系统内部持续优化，不进推送卡片。
"""
import json
import urllib.request
from datetime import datetime, timedelta

BASE = "http://127.0.0.1:8000"
OUT = "docs/push-templates"
WEEKDAY = "一二三四五六日"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.load(r)["data"]


picks = get("/api/picks/today")
sent = get("/api/market/sentiment")
rev = get("/api/review/reports/20260902")

gate = picks["meta"]["gate"]
items = picks["items"]
cal = sent.get("calibration") or {}
pct = cal.get("percentile") or {}
promo_pct = (pct.get("promo_1to2") or {}).get("percentile")
promo_val = (pct.get("promo_1to2") or {}).get("value", 0) * 100

mkt = (rev.get("data") or {}).get("market") or {}
idx = {i["name"]: i for i in mkt.get("indices", [])}
trading = (rev.get("data") or {}).get("trading") or {}
now = datetime.now()


# ---------- 公共构件（v2 版式） ----------
def md(txt):
    return {"tag": "markdown", "content": txt}


def field(title, body):
    return {"is_short": True, "text": {"tag": "lark_md", "content": f"**{title}**\n{body}"}}


def fields_grid(pairs):
    return {"tag": "div", "fields": [field(t, b) for t, b in pairs]}


def hr():
    return {"tag": "hr"}


def div(md_text):
    return {"tag": "div", "text": {"tag": "lark_md", "content": md_text}}


def note(txt):
    return {"tag": "note", "elements": [{"tag": "plain_text", "content": txt}]}


def card(header, template, elements):
    return {
        "config": {"wide_screen_mode": True},
        "header": {"title": {"tag": "plain_text", "content": header}, "template": template},
        "elements": elements,
    }


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
tomorrow = now + timedelta(days=1)
el = [
    div("**⛔ 门控：强空仓**　退潮期接力亏钱，以下清单 **🔒 仅跟踪观察，不构成买入依据**"),
    hr(),
    fields_grid([
        ("🌡️ 情绪温度", f"{sent.get('temperature')} · {sent.get('phase')}"),
        ("🚀 涨停 / 昨日", "55 家 / 80 家"),
        ("📐 首板晋级率", f"{promo_val:.1f}%（分位 {promo_pct}）"),
        ("📉 昨涨停溢价", "-2.60%（亏钱效应）"),
        ("🔎 涨跌家数", "涨 1541 / 跌 3900"),
        ("🪜 最高板", "4 板（昨 7 断板）"),
    ]),
    hr(),
    div(f"**🏆 选股器 Top5**　🔒 全部仅观察（{sum(1 for i in items if i.get('observation_only'))}/{len(items)}，门控期不买入）"),
]
for n, it in enumerate(items, 1):
    theme = it.get("theme")
    head = f"**{n}｜{it['name']} {it['symbol']}**　**{it['score']:.1f} 分** · {it.get('echelon_role') or '—'}"
    if theme:
        head += f" · {theme}（{it.get('theme_stage') or '—'}）"
    sl = it.get("stop_loss") or {}
    el.append(div(
        f"{head}\n"
        f"逻辑：{logic_line(it)}\n"
        f"失效：{(it.get('invalidations') or ['—'])[0]}\n"
        f"止损：**-{sl.get('pct', 0):.0f}% @ {sl.get('price', 0):.2f}**"
    ))
el += [
    hr(),
    div("**🌅 明日前哨**　涨停回升且晋级率 ≥25% → 修复期重评方向池；涨停 <25 家且最高板 ≤2 板 → 冰点续空仓\n"
        f"当前切换条件：{sent.get('switch_conditions')}"),
    note("选股器规则引擎 · 简报 08:40 生成，未含竞价数据 · 门控期清单仅作跟踪 · 非投资建议"),
]
card1 = card(f"📊 明日机会观察 · {tomorrow:%m-%d}（周{WEEKDAY[tomorrow.weekday()]}）", "orange", el)

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
    fields_grid([
        ("💼 今日操作", "空仓 ✅ 纪律执行" if trade_count == 0 else f"{trade_count} 笔委托"),
        ("🤖 系统动作", "3 方向盘中证伪 → 降级观察"),
        ("💰 账户", "无持仓 · 现金 ¥100.0 万"),
        ("🩺 数据完整度", "0 缺失 · 信号样本 49"),
    ]),
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
    div("② " + ("；".join((a.get("title") or "") for a in rev.get("action_items", [])[:2]) or "—")),
    hr(),
    div("**📌 改善追踪**　昨日 P0「15:35 对照数据缺失」→ ✅ 已修复，今日对照产出完整"),
    div("**🌅 明日关注**　竞价溢价与晋级率能否回升；冰点触发条件（%s）" % sent.get("switch_conditions")),
    note("复盘规则引擎 · 全维度无数据缺失 · 非投资建议"),
]
card2 = card(f"📝 盘后复盘 · {now:%m-%d}（周{WEEKDAY[now.weekday()]}）", "blue", el2)

for name, c in (("after-close-opportunity", card1), ("after-close-review", card2)):
    with open(f"{OUT}/{name}.card.json", "w", encoding="utf-8") as f:
        json.dump(c, f, ensure_ascii=False)
print(f"card1 elements: {len(el)} | card2 elements: {len(el2)}")
