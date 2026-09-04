"""组装飞书 interactive 卡片：①明日机会观察（盘后）②盘后复盘。

两种运行模式（automation 按 argv 区分）：
- 默认（盘后 15:40）：复盘报告取当日，产出 after-close-opportunity.card.json
  （机会观察，日期头=次日）+ after-close-review.card.json（盘后复盘）
- --morning（盘前 09:26）：不取复盘报告（当日尚未生成），产出
  morning-opportunity.card.json（机会观察，日期头=当日，即「今日跟踪清单」）
- 交易日哨兵：picks.date 非今日（节假日/简报未更新）→ 打印 SKIP 并退出，不推送

数据全部来自 8000 实例实时 API；输出到 docs/push-templates/。
发送（自动化侧）：lark-cli im +messages-send --user-id <open_id> --as bot \\
    --msg-type interactive --content "$(cat docs/push-templates/xxx.card.json)"

布局（v2 定稿版式，2026-09-02 用户确认）：
- 指标区用飞书原生 fields 双列栅格（is_short），标题粗体一行 + 数值一行，不挤行
- 个股块纯 markdown 多行：名称/分数/止损加粗，逻辑、失效、止损各占一行
- hr 只做模块分界

⚠️ 消息面/新闻模块已按用户 2026-09-02 安排移除：推送不含任何新闻内容，
个股筛选（多维度综合考量）保留在系统内部持续优化，不进推送卡片。
后续优化约定：发现可提升效果/美观的改进 → 主动提出（改动+理由+预期效果），
经用户确认后再执行。
"""
import json
import sys
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

# 宽度/情绪历史/账户：带来源的真实指标（缺失显式降级为 "--"，绝不硬编码）
def try_get(path):
    try:
        return get(path)
    except Exception as exc:
        print(f"WARN: {path} unavailable: {exc}")
        return None

breadth = (try_get("/api/market/breadth") or {}).get("breadth") or {}
sent_hist = try_get("/api/market/sentiment-history?days=2") or {}
hist_items = sent_hist.get("items") or sent_hist.get("history") or []
prev_sent = hist_items[-2] if len(hist_items) >= 2 else None
account = try_get("/api/paper/account") or {}

# 情绪指标取值（name → value）：涨停家数/连板家数/连板高度/昨日涨停今日中位
def ind(name):
    for i in sent.get("indicators") or []:
        if i.get("name") == name:
            return i.get("value")
    return None

lim_up = ind("涨停家数")
lim_conn = ind("连板家数")
max_boards = ind("连板高度")
yesterday_mid = ind("昨日涨停今日中位")

# 执行闸门（P0-A）：9:25 竞价结束即知的三态执行状态。失败优雅降级——
# 卡片照发，执行状态整块标"未知"，绝不因闸门端点抖动丢掉整份清单。
exec_gate = None
try:
    exec_gate = get("/api/picks/execution-gate")
except Exception as exc:
    print(f"WARN: execution-gate unavailable: {exc}")
exec_by_sym = {}
if exec_gate:
    exec_by_sym = {i["symbol"]: i for i in exec_gate.get("items") or []}
STATE_TAG = {"blocked": "🚫 禁买", "observe": "⚠️ 观察", "normal": "✅ 可买",
             "anomaly": "❗ 异常", "unknown": "❓ 未知"}

now = datetime.now()
today = now.date()
MORNING = "--morning" in sys.argv

# 交易日哨兵：picks.date 非今日 → 节假日/简报未更新，直接退出不推送
pick_date = str(picks.get("date") or "")
if pick_date != f"{today:%Y-%m-%d}":
    print(f"SKIP: picks date {pick_date!r} != today {today:%Y-%m-%d}（非交易日或未更新，不推送）")
    sys.exit(0)

# 复盘报告仅盘后模式需要（morning 模式当日复盘尚未生成；日期动态避免定时跑取旧数据）。
# 优雅降级：15:35 复盘延迟/失败时卡片照发，卡②显式标注「复盘数据缺失」，不整卡崩溃。
rev = None
if not MORNING:
    try:
        rev = get(f"/api/review/reports/{today:%Y%m%d}")
    except Exception as exc:
        print(f"WARN: review report unavailable: {exc}")

gate = picks["meta"]["gate"]
items = picks["items"]
cal = sent.get("calibration") or {}
pct = cal.get("percentile") or {}
promo_pct = (pct.get("promo_1to2") or {}).get("percentile")
promo_val = (pct.get("promo_1to2") or {}).get("value", 0) * 100

mkt = idx = trading = None
if not MORNING and rev:
    mkt = (rev.get("data") or {}).get("market") or {}
    idx = {i["name"]: i for i in mkt.get("indices", [])}
    trading = (rev.get("data") or {}).get("trading") or {}


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


# ---------- 卡片①：机会观察（morning=当日跟踪清单；盘后=明日清单） ----------
show = now if MORNING else now + timedelta(days=1)
# 门控横幅接真实 gate 判定（曾经硬编码"强空仓"，会与实际 gate 状态不符）
gate_level = (gate or {}).get("level")
gate_stand = bool((gate or {}).get("stand_aside"))
observe_n = sum(1 for i in items if i.get("observation_only"))
if gate_stand:
    gate_head = "**⛔ 门控：强空仓**" if gate_level == "strong" else "**⛔ 门控：空仓观察**"
    gate_body = "门控条件触发，以下清单 **🔒 仅跟踪观察，不构成买入依据**"
else:
    gate_head = "**✅ 门控：正常**"
    gate_body = "未触发空仓闸门；各标的执行状态以盘前竞价闸门为准"
el = [
    div(f"{gate_head}　{gate_body}"),
    hr(),
    fields_grid([
        ("🌡️ 情绪温度", f"{sent.get('temperature')} · {sent.get('phase')}"),
        ("🚀 涨停 / 连板", f"{lim_up if lim_up is not None else '--'} 家 / {lim_conn if lim_conn is not None else '--'} 家连板"),
        ("📐 首板晋级率", f"{promo_val:.1f}%（分位 {promo_pct}）"),
        ("📉 昨涨停溢价", f"{yesterday_mid:+.2f}%（中位）" if yesterday_mid is not None else "--"),
        ("🔎 涨跌家数", f"涨 {breadth.get('up', '--')} / 跌 {breadth.get('down', '--')}"),
        ("🪜 最高板", f"{max_boards}" if max_boards is not None else "--"),
    ]),
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
    exec_row = exec_by_sym.get(it["symbol"])
    exec_tag = STATE_TAG.get((exec_row or {}).get("state"), "❓ 未知") if MORNING else ""
    exec_line = ""
    if MORNING:
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
        f"当前切换条件：{sent.get('switch_conditions')}"),
    note("选股器规则引擎 · 简报 08:40 生成 · 执行状态=9:25 竞价闸门（禁买=一字/超高开）· 非投资建议"),
]
card1 = card(f"📊 {'今日' if MORNING else '明日'}机会观察 · {show:%m-%d}（周{WEEKDAY[show.weekday()]}）", "orange", el)

# ---------- 卡片②：盘后复盘（仅盘后模式；报告缺失时显式降级，不发旧数据） ----------
if not MORNING:
    if rev:
        dims = {d["key"]: d for d in rev.get("dimensions", [])}
        mkt_dim = dims.get("market") or {}
        trade_count = trading.get("trade_count", 0)
        gaps = trading.get("gaps") or []
        actions = rev.get("action_items") or []
        status_n = {}
        for a in actions:
            status_n[a.get("status") or "pending"] = status_n.get(a.get("status") or "pending", 0) + 1
        status_line = " / ".join(f"{k} {v}" for k, v in sorted(status_n.items())) or "—"
        cash = account.get("cash")
        mv = account.get("market_value")
        pnl_pct = account.get("total_pnl_pct")

        def idx_line(name):
            i = idx.get(name) or {}
            chg = i.get("change_pct")
            dot = "🔴" if (chg or 0) > 0 else ("🟢" if (chg or 0) < 0 else "⚪")
            return f"{name} {i.get('close', '-')} ({chg:+.2f}%){dot}" if chg is not None else f"{name} --"

        prev_txt = f"（昨 {prev_sent.get('phase')} {prev_sent.get('temperature')}）" if prev_sent else ""
        el2 = [
            fields_grid([
                ("💼 今日操作", "空仓 ✅ 纪律执行" if trade_count == 0 else f"{trade_count} 笔委托"),
                ("📊 复盘产出", f"{len(dims)} 维度 · 改进项 {len(actions)} 条"),
                ("💰 账户", (f"现金 ¥{cash/10000:.1f} 万 · 市值 ¥{mv/10000:.1f} 万"
                             f" · 总盈亏 {pnl_pct:+.1f}%") if pnl_pct is not None else "--"),
                ("🩺 数据完整度", f"{len(gaps)} 缺失" if gaps else "无缺失"),
            ]),
            hr(),
            div(f"**📈 盘面**　{idx_line('上证指数')}｜{idx_line('深证成指')}｜{idx_line('创业板指')}"),
            div(f"**🌡️ 情绪**　{sent.get('phase')} {sent.get('temperature')}{prev_txt}"
                f" · 晋级率 {promo_val:.1f}%（分位 {promo_pct}）"
                + (f" · 昨涨停中位 {yesterday_mid:+.2f}%" if yesterday_mid is not None else "")),
        ]
        for j in (mkt_dim.get("judgements") or [])[:2]:
            el2.append(div("**💬 研判**　" + j))
        top_insight = next((m.get("observation") for m in rev.get("meta_insights", [])
                            if isinstance(m, dict) and m.get("observation")), None)
        el2 += [
            hr(),
            div("**⚠️ 不足与改善**"),
            div("① " + (top_insight or "—")),
            div("② " + ("；".join((a.get("title") or "") for a in actions[:2]) or "—")),
            hr(),
            div(f"**📌 改进项处置**　{status_line}"),
            div("**🌅 明日关注**　竞价溢价与晋级率能否回升；切换条件（%s）" % sent.get("switch_conditions")),
            note(f"复盘规则引擎 · 模型 {rev.get('model', {}).get('actual') or '--'} · 非投资建议"),
        ]
    else:
        el2 = [
            div("**⚠️ 复盘报告尚未生成**　15:35 自动复盘延迟或失败——本卡片不含当日复盘结论，报告生成后以研究页为准。"),
            hr(),
            div(f"**🌡️ 情绪**　{sent.get('phase')} {sent.get('temperature')} · 晋级率 {promo_val:.1f}%（分位 {promo_pct}）"),
            note("复盘规则引擎 · 数据缺失降级模式 · 非投资建议"),
        ]
    card2 = card(f"📝 盘后复盘 · {now:%m-%d}（周{WEEKDAY[now.weekday()]}）", "blue", el2)

if MORNING:
    with open(f"{OUT}/morning-opportunity.card.json", "w", encoding="utf-8") as f:
        json.dump(card1, f, ensure_ascii=False)
    print(f"morning-opportunity.card.json elements: {len(el)}")
else:
    for name, c in (("after-close-opportunity", card1), ("after-close-review", card2)):
        with open(f"{OUT}/{name}.card.json", "w", encoding="utf-8") as f:
            json.dump(c, f, ensure_ascii=False)
    print(f"card1 elements: {len(el)} | card2 elements: {len(el2)}")
