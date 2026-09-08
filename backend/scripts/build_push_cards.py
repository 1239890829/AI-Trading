"""组装飞书 interactive 卡片——仅两类：①盘中跟踪·每日精选 ②盘中跟踪·盘中确认。

按用户 2026-09-07 指示收敛：每日精选并入盘中跟踪体系统一管理（盘前选出 → 盘中
确认，避免开盘即回落被误判）；**盘后复盘卡已移除**（仅推送两类）。

三种运行模式（automation 按 argv 区分）：
- --morning（盘前 09:26）：「盘中跟踪 · 每日精选」当日名单 + 竞价执行闸门
  → morning-opportunity.card.json
- --intraday（盘中 9:40/10:40/13:40/14:40）：「盘中跟踪 · 盘中确认」watcher
  确认/证伪提醒 + intraday-top 最推荐标的 + 每日精选标的盘中表现（回落显式标注）
  → intraday-tracking.card.json
- 默认（盘后 15:40）：「盘中跟踪 · 每日精选前瞻」次日日期头（次日名单 08:40
  生成，本卡为基于今日盘面的前瞻）→ after-close-opportunity.card.json

哨兵：
- 交易日哨兵（全部模式）：picks.date 非今日（节假日/简报未更新）→ 打印 SKIP 退出
- 交易时段哨兵（仅 --intraday）：北京时间不在 09:30-11:30 / 13:00-15:00 → SKIP

数据全部来自 8000 实例实时 API；输出到 docs/push-templates/。
发送（自动化侧）：lark-cli im +messages-send --user-id <open_id> --as bot \\
    --msg-type interactive --content "$(cat docs/push-templates/xxx.card.json)"

布局（v2 定稿版式，2026-09-02 用户确认）：
- 指标区用飞书原生 fields 双列栅格（is_short），标题粗体一行 + 数值一行，不挤行
- 个股块纯 markdown 多行：名称/分数/止损加粗，逻辑、失效、止损各占一行
- hr 只做模块分界

⚠️ 消息面/新闻模块已按用户 2026-09-02 安排移除：推送不含任何新闻内容。
后续优化约定：发现可提升效果/美观的改进 → 主动提出（改动+理由+预期效果），
经用户确认后再执行。
"""
import json
import sys
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # backend/ → import app.*

# 卡片构建单点：app/picks/push_cards.py（2026-09-08 抽取，盘中买点推送共用同版式）
from app.picks.push_cards import (  # noqa: E402
    WEEKDAY,
    build_picks_card,
    card,
    div,
    fields_grid,
    field,
    fmt_chg,
    first_clause,
    gate_banner,
    hr,
    ind,
    logic_line,
    note,
    sentiment_pairs,
)


BASE = "http://127.0.0.1:8000"
OUT = "docs/push-templates"
WATCHER_RULE_NAME = "__picks_watcher__"


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.load(r)["data"]


def try_get(path):
    try:
        return get(path)
    except Exception as exc:
        print(f"WARN: {path} unavailable: {exc}")
        return None


# ---------- 卡片：盘中确认（watcher 事件 + intraday-top + 每日精选盘中表现） ----------
def pick_chg(symbol):
    """每日精选标的盘中涨跌幅：change_pct 缺失时用 price/prev_close 自算（数据源纪律）。"""
    q = try_get(f"/api/quotes/{symbol}")
    if not q:
        return None, None
    chg = q.get("change_pct")
    if chg is None and q.get("price") and q.get("prev_close"):
        chg = (q["price"] / q["prev_close"] - 1) * 100
    return q.get("price"), chg


def chg_tag(chg):
    """盘中状态标注：回落显式标注（避免开盘即回落被误判），None 三态保留。"""
    if chg is None:
        return ""
    if chg <= -1:
        return "　⚠️ **盘中回落**"
    if chg < 0:
        return "　⚠️ 微跌"
    if chg >= 5:
        return "　🔥 强势"
    return ""


def _text_line(text, prefix):
    for ln in (text or "").splitlines():
        if ln.startswith(prefix):
            return ln[len(prefix):].strip()
    return None


def build_intraday_card(picks, sent, breadth, now):
    # -- watcher 确认/证伪提醒（系统规则 __picks_watcher__，仅今日） --
    rules = try_get("/api/alerts/rules") or []
    watcher_rule = next((r for r in rules if r.get("name") == WATCHER_RULE_NAME), None)
    events = []
    if watcher_rule:
        raw = try_get(f"/api/alerts/events?rule_id={watcher_rule['id']}&limit=200") or []
        for e in raw:
            try:
                t = datetime.fromisoformat(str(e.get("triggered_at", "")))
            except ValueError:
                continue
            bj = t + timedelta(hours=8)  # 库内 UTC naive → 北京
            if bj.date() != now.date():
                continue
            snap = e.get("snapshot") or {}
            if snap.get("kind") not in ("confirm", "falsify"):
                continue
            events.append((bj, e, snap))
    events.sort(key=lambda x: x[0])

    # -- intraday-top 最推荐标的（与工作台动态分组同口径；data 为 dict，列表在 items 键） --
    top = ((try_get("/api/picks/intraday-top?limit=6") or {}).get("items")) or []

    el = [fields_grid(sentiment_pairs(sent, breadth)), hr()]
    el.append(div(f"**🔔 盘中提醒**（watcher 确认/证伪 · 今日 {len(events)} 条）"))
    if events:
        for bj, e, snap in events:
            hm = f"{bj:%H:%M}"
            tv, th = e.get("trigger_value"), e.get("threshold")
            tv_txt = f"{tv:+.2f}%" if isinstance(tv, (int, float)) else "--"
            if snap["kind"] == "confirm":
                stock_line = _text_line(snap.get("text"), "1. 个股：") or ""
                name = stock_line.split("（")[0].strip()
                sym = e.get("symbol") or ""
                met = _text_line(snap.get("text"), "2. 触发指标：") or "—"
                head = f"✅ **{name or sym}（{sym}）**" if name else f"✅ **{sym}**"
                el.append(div(
                    f"{head}　方向 **{snap.get('direction')}** 确认 · {hm}\n"
                    f"触发：{met}\n"
                    f"板块涨幅 {tv_txt}（确认阈值 {th if th is not None else '--'}%）"
                ))
            else:
                detail = _text_line(snap.get("text"), "触发：") or "—"
                el.append(div(
                    f"❌ **方向证伪：{snap.get('direction')}** · {hm}\n"
                    f"触发：{detail}\n"
                    f"板块涨幅 {tv_txt}（证伪线 {th if th is not None else '--'}%）"
                ))
    else:
        el.append(div("今日暂无确认/证伪提醒——方向未达确认阈值，或已证伪停止跟踪（显式留空，不冒充正常）。"))
    el.append(hr())

    el.append(div(f"**🎯 盘中最推荐**（确定性优先切片 · {len(top)} 只）"))
    if top:
        for s in top:
            cert = (s.get("certainty") or {}).get("level") or "—"
            dist = (s.get("distinctiveness") or {}).get("level") or "—"
            boards = s.get("boards")
            role = s.get("role") or "—"
            el.append(div(
                f"T{s.get('tier', '?')}｜**{s.get('name')} {s.get('symbol')}**　{fmt_chg(s.get('change_pct'))}"
                f" · {role}" + (f"（{boards} 板）" if boards is not None else "") + "\n"
                f"题材：{s.get('theme') or '—'}（{s.get('stage') or '—'}）· 确定性 {cert} / 辨识度 {dist}\n"
                f"依据：{s.get('pick_basis') or '—'}"
            ))
    else:
        el.append(div("盘中最推荐暂无入选——确定性/辨识度未达标（unknown 不冒充机会）。"))
    el.append(hr())

    el.append(div("**📋 每日精选 · 盘中表现**（盘前名单现价对照）"))
    for n, it in enumerate(picks.get("items") or [], 1):
        price, chg = pick_chg(it["symbol"])
        price_txt = f"{price:.2f}" if price is not None else "--"
        el.append(div(
            f"**{n}｜{it['name']} {it['symbol']}**　现价 {price_txt} · {fmt_chg(chg)}{chg_tag(chg)}"
        ))
    el += [
        hr(),
        note("盘中跟踪规则引擎 · watcher 方向确认/证伪 + 选股器盘中切片 · 名单回落不等于机会失效，以失效条件为准 · 非投资建议"),
    ]
    return card(f"📈 盘中跟踪 · 盘中确认 · {now:%m-%d %H:%M}", "green", el)


# ---------- 主流程 ----------
now = datetime.now()
today = now.date()
MORNING = "--morning" in sys.argv
INTRADAY = "--intraday" in sys.argv

picks = get("/api/picks/today")

# 交易日哨兵：picks.date 非今日 → 节假日/简报未更新，直接退出不推送
pick_date = str(picks.get("date") or "")
if pick_date != f"{today:%Y-%m-%d}":
    print(f"SKIP: picks date {pick_date!r} != today {today:%Y-%m-%d}（非交易日或未更新，不推送）")
    sys.exit(0)

# 交易时段哨兵（仅盘中模式）：午休/收盘/盘后一律不推
if INTRADAY:
    hm = now.hour * 100 + now.minute
    if not (930 <= hm <= 1130 or 1300 <= hm <= 1500):
        print(f"SKIP: 当前 {now:%H:%M} 不在交易时段（09:30-11:30 / 13:00-15:00），不推送")
        sys.exit(0)

sent = try_get("/api/market/sentiment") or {}
breadth = (try_get("/api/market/breadth") or {}).get("breadth") or {}

if INTRADAY:
    card_out = build_intraday_card(picks, sent, breadth, now)
    with open(f"{OUT}/intraday-tracking.card.json", "w", encoding="utf-8") as f:
        json.dump(card_out, f, ensure_ascii=False)
    print("intraday-tracking.card.json written")
elif MORNING:
    exec_gate = None
    try:
        exec_gate = get("/api/picks/execution-gate")
    except Exception as exc:
        print(f"WARN: execution-gate unavailable: {exc}")
    card_out = build_picks_card(
        picks, sent, breadth, exec_gate,
        show=now, title_prefix="盘中跟踪 · 每日精选", with_exec=True,
    )
    with open(f"{OUT}/morning-opportunity.card.json", "w", encoding="utf-8") as f:
        json.dump(card_out, f, ensure_ascii=False)
    print("morning-opportunity.card.json written")
else:
    card_out = build_picks_card(
        picks, sent, breadth, None,
        show=now + timedelta(days=1), title_prefix="盘中跟踪 · 每日精选前瞻", with_exec=False,
    )
    with open(f"{OUT}/after-close-opportunity.card.json", "w", encoding="utf-8") as f:
        json.dump(card_out, f, ensure_ascii=False)
    print("after-close-opportunity.card.json written（盘后复盘卡已按用户 2026-09-07 指示移除）")
