"""build_push_cards.py 推送卡片脚本的全流程测试（无网络，stub urlopen）。

被测脚本是平铺脚本（模块级执行主流程），测试用 exec + monkeypatch 方式驱动：
- monkeypatch datetime.datetime → FakeDateTime（控制「今天/盘中等哨兵」）
- monkeypatch urllib.request.urlopen → 按 URL 路由的假响应
- monkeypatch.chdir(tmp_path)（OUT 相对路径落盘到临时目录，不污染真实模板）
- SystemExit（SKIP 路径 sys.exit(0)）被捕获并记入 __exit_code__
"""
from __future__ import annotations

import datetime as dt_mod
import json
import sys
import urllib.request
from pathlib import Path
from urllib.error import URLError

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "build_push_cards.py"

WATCHER_RULE = {"id": 7, "name": "__picks_watcher__"}
CONFIRM_TEXT = (
    "【盘中机会】方向：AI漫剧\n"
    "1. 个股：天沃科技（002564）· 梯队角色：龙头\n"
    "2. 触发指标：板块涨幅 ≥2% · 封单留存达标\n"
    "3. 逻辑：盘前方向盘中确认走强"
)


class FakeResp:
    def __init__(self, payload):
        self._bytes = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._bytes

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _pick(symbol="002564", name="天沃科技"):
    return {
        "name": name,
        "symbol": symbol,
        "score": 82.5,
        "echelon_role": "龙头",
        "theme": "国资改革",
        "theme_stage": "启动",
        "stop_loss": {"pct": 5, "price": 10.5},
        "invalidations": ["跌破分时均线"],
        "bases": {"echelon": "题材梯队龙头；封板质量高", "fundamental": "净利同比 +30%"},
        "observation_only": False,
    }


def _fake_env(*, pick_date="2026-09-07", confirm_event=False):
    """按 URL 路由的假后端。时间基线：2026-09-07（周一，交易日）。"""
    events = []
    if confirm_event:
        # 库内 UTC naive → +8 后 = 2026-09-07 10:47 北京（当日盘中）
        events.append(
            {
                "id": 1,
                "rule_id": WATCHER_RULE["id"],
                "symbol": "002564",
                "trigger_value": 2.31,
                "threshold": 2.0,
                "triggered_at": "2026-09-07T02:47:00",
                "acknowledged": False,
                "delivered_channels": ["in_app"],
                "snapshot": {"kind": "confirm", "direction": "AI漫剧", "text": CONFIRM_TEXT},
            }
        )

    def router(url, timeout=15):
        path = url.split("127.0.0.1:8000", 1)[1]
        payload = None
        if path == "/api/picks/today":
            payload = {
                "date": pick_date,
                "meta": {"gate": {"level": "normal", "stand_aside": False}},
                "items": [_pick()],
            }
        elif path == "/api/market/sentiment":
            payload = {
                "temperature": 60,
                "phase": "修复",
                "indicators": [{"name": "涨停家数", "value": 66}],
                "switch_conditions": "切换条件示例",
                "calibration": {"percentile": {"promo_1to2": {"percentile": 55, "value": 0.30}}},
            }
        elif path == "/api/market/breadth":
            payload = {"breadth": {"up": 3200, "down": 1800}}
        elif path == "/api/picks/execution-gate":
            payload = {"items": [{"symbol": "002564", "state": "normal", "reason": "竞价正常"}]}
        elif path.startswith("/api/alerts/rules"):
            payload = [WATCHER_RULE]
        elif path.startswith("/api/alerts/events"):
            payload = events
        elif path.startswith("/api/picks/intraday-top"):
            payload = {
                "items": [
                    {
                        "symbol": "002564",
                        "name": "天沃科技",
                        "role": "龙头",
                        "boards": 2,
                        "change_pct": 9.94,
                        "theme": "国资改革",
                        "stage": "启动",
                        "tier": 2,
                        "pick_basis": "确定性高：题材阶段与封板质量支持延续",
                        "certainty": {"level": "高"},
                        "distinctiveness": {"level": "中"},
                    }
                ]
            }
        elif path.startswith("/api/quotes/"):
            # change_pct 缺失 → 脚本须用 price/prev_close 自算（+10.04%）
            payload = {"symbol": "002564", "name": "天沃科技", "price": 12.49, "prev_close": 11.35, "change_pct": None}
        else:
            raise URLError(path)
        return FakeResp({"data": payload})  # get() 统一剥 Envelope.data

    return router


def _run_script(monkeypatch, capsys, fake_now, argv, *, pick_date="2026-09-07"):
    """以假时钟+假后端 exec 被测脚本（cwd 已切到 tmp_path）。

    返回 (stdout, globals)：globals 含脚本定义的函数与 __exit_code__（SKIP 时为 0）。
    """

    class FakeDT(dt_mod.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt_mod.datetime(*fake_now)

    monkeypatch.setattr(dt_mod, "datetime", FakeDT)
    monkeypatch.setattr(urllib.request, "urlopen", _fake_env(pick_date=pick_date))
    monkeypatch.setattr(sys, "argv", argv)
    (Path("docs/push-templates")).mkdir(parents=True, exist_ok=True)
    src = SCRIPT.read_text(encoding="utf-8")
    g: dict = {"__name__": "__main__"}
    try:
        exec(compile(src, str(SCRIPT), "exec"), g)
    except SystemExit as exc:
        g["__exit_code__"] = exc.code
    return capsys.readouterr().out, g


def _card(tmp_path, name):
    return json.loads((tmp_path / "docs/push-templates" / name).read_text(encoding="utf-8"))


def test_morning_mode_writes_card_with_exec_gate(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    out, _ = _run_script(monkeypatch, capsys, (2026, 9, 7, 9, 26), ["build_push_cards.py", "--morning"])
    assert "written" in out
    card = _card(tmp_path, "morning-opportunity.card.json")
    assert "盘中跟踪 · 每日精选" in card["header"]["title"]["content"]
    assert "09-07" in card["header"]["title"]["content"]
    body = json.dumps(card, ensure_ascii=False)
    assert "执行：**✅ 可买**" in body
    assert "名单为盘中跟踪输入，机会确认以盘中提醒为准" in body


def test_intraday_out_of_session_skips(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    out, g = _run_script(monkeypatch, capsys, (2026, 9, 7, 23, 31), ["build_push_cards.py", "--intraday"])
    assert "SKIP" in out
    assert g["__exit_code__"] == 0
    assert not (tmp_path / "docs/push-templates/intraday-tracking.card.json").exists()


def test_intraday_lunch_break_skips(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    out, _ = _run_script(monkeypatch, capsys, (2026, 9, 7, 12, 0), ["build_push_cards.py", "--intraday"])
    assert "SKIP" in out


def test_non_trade_day_skips(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    # 时钟=周一 09-07，但 picks.date=上周五 09-04 → 交易日哨兵拦截（早于时段哨兵）
    out, _ = _run_script(
        monkeypatch, capsys, (2026, 9, 7, 9, 26), ["build_push_cards.py", "--morning"], pick_date="2026-09-04"
    )
    assert "SKIP" in out


def test_intraday_no_events_explicit_empty(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    argv = ["build_push_cards.py", "--intraday"]
    out, g = _run_script(monkeypatch, capsys, (2026, 9, 7, 10, 40), argv)
    assert "written" in out
    card = _card(tmp_path, "intraday-tracking.card.json")
    body = json.dumps(card, ensure_ascii=False)
    # 今日无 watcher 事件 → 显式留空文案（三态纪律，不冒充正常）
    assert "今日暂无确认/证伪提醒" in body
    # intraday-top 行
    assert "T2｜**天沃科技 002564**" in body
    assert "确定性 高 / 辨识度 中" in body
    # 每日精选盘中表现：quotes change_pct 缺失 → price/prev_close 自算 = +10.04%
    assert "现价 12.49 · +10.04%" in body


def test_intraday_confirm_event_rendered(monkeypatch, capsys, tmp_path):
    """confirm 事件行：从 snapshot.text 提取个股名/触发指标，事件时间 UTC+8。"""
    monkeypatch.chdir(tmp_path)
    router = _fake_env(confirm_event=True)

    class FakeDT(dt_mod.datetime):
        @classmethod
        def now(cls, tz=None):
            return dt_mod.datetime(2026, 9, 7, 10, 50)

    monkeypatch.setattr(dt_mod, "datetime", FakeDT)
    monkeypatch.setattr(urllib.request, "urlopen", router)
    monkeypatch.setattr(sys, "argv", ["build_push_cards.py", "--intraday"])
    (Path("docs/push-templates")).mkdir(parents=True, exist_ok=True)
    g: dict = {"__name__": "__main__"}
    try:
        exec(compile(SCRIPT.read_text(encoding="utf-8"), str(SCRIPT), "exec"), g)
    except SystemExit as exc:
        g["__exit_code__"] = exc.code
    card = _card(tmp_path, "intraday-tracking.card.json")
    body = json.dumps(card, ensure_ascii=False)
    assert "✅ **天沃科技（002564）**" in body
    assert "方向 **AI漫剧** 确认" in body
    assert "10:47" in body
    assert "板块涨幅 ≥2% · 封单留存达标" in body
    assert "+2.31%" in body


def test_after_close_writes_single_card_no_review(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)
    out, _ = _run_script(monkeypatch, capsys, (2026, 9, 7, 15, 40), ["build_push_cards.py"])
    assert "after-close-opportunity.card.json written" in out
    assert "复盘卡已按用户 2026-09-07 指示移除" in out
    tdir = tmp_path / "docs/push-templates"
    assert (tdir / "after-close-opportunity.card.json").exists()
    assert not (tdir / "after-close-review.card.json").exists()
    card = _card(tmp_path, "after-close-opportunity.card.json")
    # 日期头=次日（周二 09-08）
    assert "09-08" in card["header"]["title"]["content"]
    assert "每日精选前瞻" in card["header"]["title"]["content"]
    assert "次日名单 08:40 生成" in card["elements"][-1]["elements"][0]["content"]


def test_pure_helpers():
    """纯函数（fmt_chg/chg_tag/_text_line）断言——从源码切两段函数定义 exec 后取用。"""
    src = SCRIPT.read_text(encoding="utf-8")
    seg = (
        src[src.index("def fmt_chg"): src.index("# ---------- 公共构件")]
        + src[src.index("def pick_chg"): src.index("def build_intraday_card")]
    )
    ns: dict = {}
    exec(compile(seg, "helpers", "exec"), ns)
    fake_text = "【方向证伪】AI漫剧\n触发：接力环境证伪（promo 分位 40.7）\n当前板块涨幅：0.46%"
    assert ns["fmt_chg"](None) == "--"
    assert ns["fmt_chg"](1.234) == "+1.23%"
    assert ns["chg_tag"](None) == ""
    assert "回落" in ns["chg_tag"](-3.5)
    assert "微跌" in ns["chg_tag"](-0.5)
    assert ns["chg_tag"](1.0) == ""
    assert "强势" in ns["chg_tag"](6.0)
    assert ns["_text_line"](fake_text, "触发：") == "接力环境证伪（promo 分位 40.7）"
    assert ns["_text_line"](fake_text, "不存在：") is None
