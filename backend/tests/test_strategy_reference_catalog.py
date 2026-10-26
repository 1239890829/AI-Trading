"""Only three declared manual research adapters; no production or fake track record."""
from collections import Counter

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
import pytest

from app.api.deps import require_api_token
from app.api.routes import picks
from app.core.config import settings
from app.picks import strategy_registry as registry


REGISTERED = ("daily_picks", "intraday_watch", "pullback_reversal", "triple_volume", "two_thirty_five")
SOURCE_CODES = """
hot_theme low_rebound_quality limit_up_pullback etf_cold_rebound etf_bottom_volume
etf_momentum_rotation blue_chip_income etf_trend_takeoff dragon_head ma_golden_cross
dual_low ma_bullish limit_up_gene momentum_quality growth_quality event_driven
rsi_reversal shrink_pullback quality_value wave_theory balanced_alpha
box_first_board_breakout volume_breakout l1_l2_volume_resonance capital_heat
one_yang_three_yin macd_golden_cross chan_theory w_bottom_breakout emotion_cycle
bottom_volume box_oscillation low_volatility_quality expectation_repricing auction_breakout_2to3
""".split()
V3 = {"low_rebound_quality", "box_first_board_breakout", "l1_l2_volume_resonance",
      "w_bottom_breakout", "auction_breakout_2to3"}
ETF = {"etf_cold_rebound", "etf_bottom_volume", "etf_momentum_rotation", "etf_trend_takeoff"}
MANUAL = {"macd_golden_cross": "ashare-macd-12-26-9-v1", "ma_bullish": "ashare-ma-5-10-20-v1",
          "ma_golden_cross": "ashare-ma-cross-volume5-v1"}


@pytest.fixture
def verification(monkeypatch):
    monkeypatch.setattr(registry, "_verification_of", lambda key: {
        "available": False, "verdict": None, "headline": None, "reason": "isolated missing verification"})


def test_opt_in_preserves_default_records_and_original_runtime_registry(verification):
    before = registry.list_strategy_keys()
    expanded = registry.list_strategy_keys(include_candidates=True)
    assert [row["key"] for row in before] == list(REGISTERED)
    assert not any("catalog_kind" in row or "candidate" in row for row in before)
    assert [{key: value for key, value in row.items() if key != "catalog_kind"}
            for row in expanded[:5]] == before
    assert all(row["catalog_kind"] == "registered" for row in expanded[:5])
    assert registry.list_strategy_keys(include_candidates=False) == before
    assert tuple(spec.key for spec in registry.SPECS) == REGISTERED
    assert tuple(registry.BY_KEY) == REGISTERED
    assert set(registry.COLLECTORS) == {"daily_picks", "intraday_watch"}


def test_complete_reference_identity_versions_and_asset_scope(verification):
    rows = registry.list_strategy_keys(include_candidates=True)[5:]
    assert len(rows) == 35
    assert [row["candidate"]["source_code"] for row in rows] == SOURCE_CODES
    assert len({row["key"] for row in rows}) == 35
    assert Counter(row["candidate"]["asset"] for row in rows) == {"stock": 31, "etf": 4}
    for row in rows:
        item = row["candidate"]
        code = item["source_code"]
        assert row["key"] == f"reference:{code}"
        assert item["source_version"] == f"20261025:v{5 if code in ETF else 3 if code in V3 else 4}"
        assert row["status"] == ("out_of_scope" if code in ETF else "research_ready" if code in MANUAL else "candidate")
        assert row["catalog_kind"] == "reference" and row["basis"] == "not_evaluated"
        if code in ETF:
            assert any("ETF范围尚未开放" in text for text in item["limitations"])


def test_reference_research_never_claims_production_execution_or_performance(verification):
    for row in registry.list_strategy_keys(include_candidates=True)[5:]:
        code = row["candidate"]["source_code"]
        manual = code in MANUAL
        assert row["evaluable"] is False
        assert row["discovery"]["supports_saved_runs"] is manual
        assert row["candidate"]["runnable"] is manual
        if manual:
            execution = row["candidate"]["execution"]
            assert execution["mode"] == "manual_research" and execution["production_enabled"] is False
            assert execution["version"] == MANUAL[code]
            assert len(execution["definition_digest"]) == 64
            assert execution["definition"] and execution["definition_supplement"]
        else:
            assert "execution" not in row["candidate"]
        assert "runtime" not in row and "verification" not in row
        assert row["discovery"]["category_label"] and row["discovery"]["session_label"]
        assert row["discovery"]["category"] in {
            "theme_event", "trend", "reversal", "pattern", "quality", "structure", "etf"}
        assert row["discovery"]["session"] in {"any", "after_hours", "intraday"}
        for field in ("criteria", "existing_coverage", "limitations"):
            assert row["candidate"][field]
            assert all(isinstance(text, str) and text for text in row["candidate"][field])
        assert all(field not in row["candidate"] for field in
                   ("win_rate", "return", "equity_curve", "picks", "tenant_id", "user_id"))
        if manual:
            assert any("不等于正式入选" in text for text in row["candidate"]["limitations"])
            assert any("禁止改生产权重或风控" in text for text in row["candidate"]["limitations"])
        else:
            assert any("未通过" in text and "不启用" in text for text in row["candidate"]["limitations"])


def test_known_numeric_conditions_and_missing_definitions_survive(verification):
    rows = {row["candidate"]["source_code"]: row["candidate"]
            for row in registry.list_strategy_keys(include_candidates=True)[5:]}
    text = lambda code: " ".join(rows[code]["criteria"] + rows[code]["limitations"])
    box = text("box_first_board_breakout")
    assert all(value in box for value in ("T-20至T-1", "15%至25%", "10%", "20%", "高于5%", "待定"))
    resonance = text("l1_l2_volume_resonance")
    assert all(value in resonance for value in ("30日", "12个交易日", "60日均量的3倍", "未完全"))
    bottom = text("w_bottom_breakout")
    assert all(value in bottom for value in ("60日", "20日低点不超过15%", "禁止借未来拐点"))
    auction = text("auction_breakout_2to3")
    assert all(value in auction for value in ("主板10%", "二连板", "真实09:25", "[1%, 3%)", "错过窗口不能回填"))
    for code in ("growth_quality", "event_driven"):
        assert "仅声明日线依赖" in text(code)
    assert "现役技术维度已有" in " ".join(rows["ma_bullish"]["existing_coverage"])


def test_mutating_a_reading_response_cannot_rewrite_catalog_or_default(verification):
    before = registry.list_strategy_keys()
    first = registry.list_strategy_keys(include_candidates=True)
    first[5]["candidate"]["criteria"].clear()
    first[5]["candidate"]["runnable"] = True
    first[5]["discovery"]["category_label"] = "mutated"
    first[0]["discovery"]["summary"] = "mutated"
    again = registry.list_strategy_keys(include_candidates=True)
    assert again[5]["candidate"]["criteria"]
    assert again[5]["candidate"]["runnable"] is False
    assert again[5]["discovery"]["category_label"] != "mutated"
    assert registry.list_strategy_keys() == before


def test_reference_browsing_does_not_extend_health_evaluation(verification, monkeypatch):
    registry.list_strategy_keys(include_candidates=True)
    seen = []
    def collect(_session_factory, key):
        seen.append(key)
        return {"strategy_key": key, "status": "warning" if key == "daily_picks" else "insufficient"}
    monkeypatch.setattr(registry, "collect_strategy_health", collect)
    payload = registry.collect_all_strategy_health(object())
    assert tuple(seen) == REGISTERED
    assert payload["counts"]["total"] == 5 and payload["counts"]["evaluable"] == 2
    assert registry.strategy_verdicts(object()) == [{"strategy_key": "daily_picks", "status": "warning"}]
    assert tuple(seen) == REGISTERED * 2


def test_reference_key_is_not_an_execution_strategy(verification):
    result = registry.collect_strategy_health(None, "reference:ma_golden_cross")
    assert result["status"] == "unknown"
    assert "未登记" in result["reason"]


@pytest.fixture
def api(verification, monkeypatch):
    calls = []
    sf = object()
    monkeypatch.setattr(picks, "get_session_factory", lambda: sf)
    def saved(owner):
        assert owner is sf
        calls.append("saved_read")
        # A broken foreign run cannot make a reference runnable or attach a history.
        return {"daily_picks": {"state": "available", "run_id": "fixture-original"},
                "intraday_watch": {"state": "none"},
                "reference:hot_theme": {"state": "available", "run_id": "forged-reference"}}
    monkeypatch.setattr(registry, "saved_method_runs", saved)
    monkeypatch.setattr(settings, "api_token", "")
    app = FastAPI()
    app.include_router(picks.router, prefix="/api", dependencies=[Depends(require_api_token)])
    return TestClient(app), calls


def test_real_api_explicit_opt_in_preserves_envelope_and_projects_only_saved_owners(api):
    client, calls = api
    default = client.get("/api/picks/strategy-registry").json()
    explicit_false = client.get("/api/picks/strategy-registry?include_candidates=false").json()
    expanded = client.get("/api/picks/strategy-registry?include_candidates=true").json()
    assert explicit_false == default
    assert set(expanded) == {"data", "meta"} and expanded["meta"] == {}
    assert len(default["data"]["strategies"]) == 5
    rows = expanded["data"]["strategies"]
    assert len(rows) == 40
    assert rows[0]["runtime"]["run_id"] == "fixture-original"
    assert rows[1]["runtime"]["state"] == "none"
    assert all("runtime" not in row for row in rows[2:])
    assert calls == ["saved_read"] * 3
    assert client.post("/api/picks/strategy-registry?include_candidates=true").status_code == 405


def test_bad_flag_cannot_trigger_a_registry_or_runtime_read(api):
    client, calls = api
    assert client.get("/api/picks/strategy-registry?include_candidates=all").status_code == 422
    assert calls == []


def test_runtime_read_failure_does_not_forge_reference_history(api, monkeypatch):
    client, _ = api
    def broken(_sf):
        raise RuntimeError("isolated saved-read failure")
    monkeypatch.setattr(registry, "saved_method_runs", broken)
    response = client.get("/api/picks/strategy-registry?include_candidates=true")
    assert response.status_code == 200
    rows = response.json()["data"]["strategies"]
    assert rows[0]["runtime"]["state"] == rows[1]["runtime"]["state"] == "error"
    assert all("runtime" not in row for row in rows[2:])
    assert rows[5]["status"] == "candidate" and rows[5]["candidate"]["runnable"] is False


def test_explicit_catalog_still_requires_ordinary_paired_read_permission(api, tmp_path, monkeypatch):
    from app.core import device_access as access

    client, calls = api
    monkeypatch.setattr(access, "STORE_PATH", tmp_path / "devices.sqlite3")
    monkeypatch.setattr(settings, "api_token", "fixture-server")
    store = access.get_device_store()
    origin = "http://catalog.test:3000"
    admin, _ = store.bootstrap(store.bootstrap_code(), "测试管理设备", origin)
    token, row = store.start("普通测试设备", origin)
    store.manage(admin, origin, row["id"], "approve")
    assert store.authorize(token, origin)["maintenance"] is False
    url = "/api/picks/strategy-registry?include_candidates=true"
    assert client.get(url).status_code == 401
    assert calls == []
    headers = {"X-API-Token": "fixture-server", access.BROWSER_HEADER: "1",
               access.SESSION_HEADER: token, access.ORIGIN_HEADER: origin}
    response = client.get(url, headers=headers)
    assert response.status_code == 200 and len(response.json()["data"]["strategies"]) == 40
    forged = {**headers, access.SESSION_HEADER: "unknown"}
    assert client.get(url, headers=forged).status_code == 401
    assert calls == ["saved_read"]
