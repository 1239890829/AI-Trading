"""Real date/version/window boundaries for daily selection observation."""
from datetime import date, datetime
import asyncio
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.bjtime import BJ_TZ
from app.models.daily_pick import DailyPickReview, DailyPickSet
from app.models.watchlist import Base
from app.picks.engine import review_entry_quality
from app.schemas.market import Quote


@pytest.fixture
def sf(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'review.db'}")
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def add_set(sf, day="2026-10-09", generated="2026-10-09T09:26:00+08:00"):
    item = {"symbol": "600127", "name": "粮食", "price": 10,
            "buy_range": {"low": 9.7, "high": 10.3}, "echelon_role": "领涨"}
    with sf() as db:
        db.add(DailyPickSet(date=day, items=json.dumps([item]),
             meta=json.dumps({"generated_at": generated, "market_phase": "发酵"})))
        db.commit()


def quote(symbol="600127", **kw):
    values = {"symbol": symbol, "source": "fixture", "quality": "high", "price": 10.8,
              "open": 10, "high": 11, "low": 9.5, "prev_close": 10,
              "change_pct": 8, "data_timestamp": datetime(2026, 10, 9, 15, tzinfo=BJ_TZ)}
    values.update(kw)
    return Quote(**values)


def hub_stub():
    from types import SimpleNamespace
    return SimpleNamespace(get_indices=lambda: [quote("000001", change_pct=0, price=100, open=100)])


def prepare(monkeypatch):
    import app.picks.daily_review as dr
    monkeypatch.setattr(dr, "beijing_now", lambda: datetime(2026, 10, 9, 15, 30, tzinfo=BJ_TZ), raising=False)
    async def prices(hub, symbols):
        return {sym: quote(sym) for sym in symbols}
    monkeypatch.setattr(dr, "batch_quotes", prices)
    import app.services.market_context as mc
    async def sentiment(*args, **kwargs):
        return {"phase": "发酵"}
    monkeypatch.setattr(mc, "compute_market_sentiment", sentiment)
    return dr


def test_iso_collector_does_not_select_future_set(sf):
    add_set(sf)
    add_set(sf, "2026-10-12", "2026-10-12T09:26:00+08:00")
    from app.review.collector import collect_picks
    snap = collect_picks(sf, date(2026, 10, 9))
    assert snap.combo_date == "2026-10-09"


def test_compact_legacy_date_can_be_read_without_future(sf):
    add_set(sf, "20261009")
    add_set(sf, "20261012", "2026-10-12T09:26:00+08:00")
    from app.review.collector import collect_picks
    snap = collect_picks(sf, date(2026, 10, 9))
    assert snap.combo_date == "2026-10-09"


def test_review_uses_requested_day_not_latest(sf, monkeypatch):
    add_set(sf)
    add_set(sf, "2026-10-12", "2026-10-12T09:26:00+08:00")
    dr = prepare(monkeypatch)
    out = asyncio.run(dr.generate_daily_review(hub_stub(), None, sf, trade_date=date(2026, 10, 9)))
    assert out["date"] == "2026-10-09"
    with sf() as db:
        rows = db.scalars(select(DailyPickReview)).all()
        assert {r.date for r in rows} == {"2026-10-09"}


def test_intraday_generation_never_claims_earlier_low_fill(sf, monkeypatch):
    add_set(sf, generated="2026-10-09T13:00:00+08:00")
    dr = prepare(monkeypatch)
    out = asyncio.run(dr.generate_daily_review(hub_stub(), None, sf))
    review = out["reviews"][0]
    assert review["entry"]["filled"] is None
    assert review["excess_pct"] is None
    assert review["verdict"] == "flat"
    assert "入选" in review["note"]


def test_history_request_never_uses_today_quote(sf, monkeypatch):
    add_set(sf, "2026-10-08", "2026-10-08T09:26:00+08:00")
    dr = prepare(monkeypatch)
    async def forbidden(*args):
        raise AssertionError("historical review must not fetch today's quote")
    monkeypatch.setattr(dr, "batch_quotes", forbidden)
    out = asyncio.run(dr.generate_daily_review(hub_stub(), None, sf, trade_date=date(2026, 10, 8)))
    assert out["state"] == "historical_read_only"
    with sf() as db:
        assert db.scalars(select(DailyPickReview)).all() == []


def test_outside_entire_buy_range_not_filled():
    out = review_entry_quality(buy_range={"low": 9.7, "high": 10.3},
                              day_open=9, day_high=9.2, day_low=8.5, day_close=9.1)
    assert out["filled"] is False


def test_neutral_review_is_flat(sf, monkeypatch):
    add_set(sf)
    dr = prepare(monkeypatch)
    async def neutral(hub, symbols):
        return {sym: quote(sym, price=10, high=10.2, low=9.8, change_pct=0) for sym in symbols}
    monkeypatch.setattr(dr, "batch_quotes", neutral)
    out = asyncio.run(dr.generate_daily_review(hub_stub(), None, sf))
    assert out["reviews"][0]["verdict"] == "flat"


def test_unbound_legacy_reviews_do_not_enter_health(sf):
    add_set(sf)
    with sf() as db:
        db.add(DailyPickReview(date="2026-10-09", symbol="600127", verdict="good", excess_pct=4))
        db.commit()
    from app.picks.signal_health import collect_daily_pick_groups
    assert collect_daily_pick_groups(sf) == []


def test_generation_changes_during_review_are_not_persisted(sf, monkeypatch):
    add_set(sf)
    dr = prepare(monkeypatch)
    async def change_generation(hub, symbols):
        with sf() as db:
            row = db.scalar(select(DailyPickSet))
            meta = json.loads(row.meta)
            meta['generated_at'] = '2026-10-09T14:00:00+08:00'
            row.meta = json.dumps(meta)
            db.commit()
        return {sym: quote(sym) for sym in symbols}
    monkeypatch.setattr(dr, 'batch_quotes', change_generation)
    out = asyncio.run(dr.generate_daily_review(hub_stub(), None, sf))
    assert out['state'] == 'superseded'
    with sf() as db:
        assert db.scalars(select(DailyPickReview)).all() == []
        assert json.loads(db.scalar(select(DailyPickSet)).meta)['generated_at'].endswith('14:00:00+08:00')


@pytest.mark.parametrize('stamp,quality', [('2026-10-08T15:00:00+08:00', 'high'),
                                         ('2026-10-09T15:00:10+08:00', 'high'),
                                         (None, 'high'), ('2026-10-09T15:00:00+08:00', 'invalid')])
def test_unknown_stale_future_or_invalid_close_never_enters_stats(sf, monkeypatch, stamp, quality):
    add_set(sf)
    dr = prepare(monkeypatch)
    async def bad_quote(hub, symbols):
        return {sym: quote(sym, data_timestamp=datetime.fromisoformat(stamp) if stamp else None, quality=quality) for sym in symbols}
    monkeypatch.setattr(dr, 'batch_quotes', bad_quote)
    out = asyncio.run(dr.generate_daily_review(hub_stub(), None, sf))
    assert out['reviews'][0]['review_context']['eligible_for_stats'] is False
    assert out['reviews'][0]['excess_pct'] is None
    from app.picks.signal_health import collect_daily_pick_groups
    assert collect_daily_pick_groups(sf) == []


def test_bound_generation_can_be_read_and_stale_version_cannot_enter_stats(sf, monkeypatch):
    add_set(sf)
    dr = prepare(monkeypatch)
    asyncio.run(dr.generate_daily_review(hub_stub(), None, sf))
    from app.review.collector import collect_picks
    snap = collect_picks(sf, date(2026, 10, 9))
    assert snap.reviews[0].statistics_eligible is True
    assert snap.reviews[0].review_context['window_kind'] == 'open_to_close'
    from app.picks.signal_health import collect_daily_pick_groups
    assert collect_daily_pick_groups(sf)[0]['n'] == 1
    with sf() as db:
        row = db.scalar(select(DailyPickSet))
        meta = json.loads(row.meta)
        meta['generated_at'] = '2026-10-09T09:28:00+08:00'
        row.meta = json.dumps(meta)
        db.commit()
    assert collect_daily_pick_groups(sf) == []
    assert collect_picks(sf, date(2026, 10, 9)).reviews[0].binding_state == 'legacy_or_superseded'


def test_new_generation_preserves_prior_bound_context(sf, monkeypatch):
    add_set(sf)
    dr = prepare(monkeypatch)
    first = asyncio.run(dr.generate_daily_review(hub_stub(), None, sf))
    original = first['reviews'][0]['review_context']
    with sf() as db:
        row = db.scalar(select(DailyPickSet))
        row.meta = json.dumps({'generated_at': '2026-10-09T13:00:00+08:00'})
        db.commit()
    out = asyncio.run(dr.generate_daily_review(hub_stub(), None, sf))
    assert out['state'] == 'completed'
    with sf() as db:
        rows = db.scalars(select(DailyPickReview).order_by(DailyPickReview.id)).all()
        assert len(rows) == 2
        assert json.loads(rows[0].review_context) == original
        assert rows[0].selection_version != rows[1].selection_version
    asyncio.run(dr.generate_daily_review(hub_stub(), None, sf))
    with sf() as db:
        assert len(db.scalars(select(DailyPickReview)).all()) == 2


def test_old_carryover_reference_never_gets_current_generation_window(sf, monkeypatch):
    add_set(sf, generated='2026-10-09T13:00:00+08:00')
    with sf() as db:
        row = db.scalar(select(DailyPickSet))
        items = json.loads(row.items)
        items[0]['quote_audit'] = {'source': 'fixture', 'quality': 'high',
                                  'data_timestamp': '2026-10-08T15:00:00+08:00'}
        row.items = json.dumps(items)
        db.commit()
    dr = prepare(monkeypatch)
    context = asyncio.run(dr.generate_daily_review(hub_stub(), None, sf))['reviews'][0]['review_context']
    assert context['reference_return_pct'] is None
    assert context['reference_window_reason']
    assert context['reference_as_of'] == '2026-10-08T15:00:00+08:00'


def test_history_iso_compact_duplicates_choose_iso_without_rewriting(sf, monkeypatch):
    add_set(sf, '2026-10-08', '2026-10-08T09:26:00+08:00')
    with sf() as db:
        db.add(DailyPickReview(date='20261008', symbol='600127', verdict='bad', note='old compact'))
        db.add(DailyPickReview(date='2026-10-08', symbol='600127', verdict='flat', note='canonical ISO'))
        db.commit()
    dr = prepare(monkeypatch)
    out = asyncio.run(dr.generate_daily_review(hub_stub(), None, sf, trade_date='20261008'))
    assert len(out['reviews']) == 1
    assert out['reviews'][0]['note'] == 'canonical ISO'
    with sf() as db:
        assert len(db.scalars(select(DailyPickReview)).all()) == 2


def test_collector_current_bound_row_wins_over_older_same_symbol_legacy(sf, monkeypatch):
    add_set(sf)
    with sf() as db:
        db.add(DailyPickReview(date='2026-10-09', symbol='600127', verdict='bad', note='unbound legacy'))
        db.commit()
    dr = prepare(monkeypatch)
    asyncio.run(dr.generate_daily_review(hub_stub(), None, sf))
    from app.review.collector import collect_picks
    snap = collect_picks(sf, date(2026, 10, 9))
    assert len(snap.reviews) == 1
    assert snap.reviews[0].binding_state == 'bound'
    assert snap.reviews[0].statistics_eligible is True
    from app.api.routes import picks
    monkeypatch.setattr(picks, 'get_session_factory', lambda: sf)
    out = asyncio.run(picks.list_reviews(date_str='2026-10-09', limit=1, version=None))
    assert out['data'][0]['selection_version']
    assert out['data'][0]['statistics_eligible'] is True
    assert out['meta']['legacy_duplicates_hidden'] == 1


def test_api_default_current_and_explicit_version_preserve_original_context(sf, monkeypatch):
    add_set(sf)
    dr = prepare(monkeypatch)
    first = asyncio.run(dr.generate_daily_review(hub_stub(), None, sf))
    original = first['reviews'][0]['review_context']
    with sf() as db:
        row = db.scalar(select(DailyPickSet))
        row.meta = json.dumps({'generated_at': '2026-10-09T13:00:00+08:00'})
        items = json.loads(row.items)
        items[0]['echelon_role'] = '滞涨'
        row.items = json.dumps(items)
        db.commit()
    asyncio.run(dr.generate_daily_review(hub_stub(), None, sf))
    from app.api.routes import picks
    monkeypatch.setattr(picks, 'get_session_factory', lambda: sf)
    latest = asyncio.run(picks.list_reviews(date_str='20261009', limit=60, version=None))
    assert len(latest['data']) == 1
    assert latest['data'][0]['review_context']['generated_at'] == '2026-10-09T13:00:00+08:00'
    assert latest['data'][0]['statistics_eligible'] is False
    old = asyncio.run(picks.list_reviews(date_str='2026-10-09', limit=60, version=original['selection_version']))
    assert len(old['data']) == 1
    assert old['data'][0]['review_context'] == original
    assert old['data'][0]['review_context']['echelon_role'] == '领涨'
    assert old['data'][0]['statistics_eligible'] is False


def test_shadow_history_date_aliases_cannot_inflate_sample_days(sf):
    add_set(sf, '20261008', '2026-10-08T09:26:00+08:00')
    add_set(sf, '2026-10-08', '2026-10-08T09:26:00+08:00')
    add_set(sf, '2026-10-07', '2026-10-07T09:26:00+08:00')
    from app.services.shadow_eval import load_history, eval_min_pick_score
    sets, reviews, error = load_history(sf)
    assert error is None
    assert [s['date'] for s in sets] == ['2026-10-08', '2026-10-07']
    out = eval_min_pick_score(before=50, after=45, sets=sets, reviews=reviews)
    assert out['verdict'] == 'insufficient'
    short, _, error = load_history(sf, limit=2)
    assert error is None
    assert [s['date'] for s in short] == ['2026-10-08', '2026-10-07']


def test_old_head_upgrade_keeps_legacy_row_unbound_and_column_nullable(tmp_path):
    from alembic import command
    from alembic.config import Config
    from pathlib import Path
    from sqlalchemy import inspect, text
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE daily_pick_review (id INTEGER PRIMARY KEY, date VARCHAR(10), symbol VARCHAR(12), name VARCHAR(64), verdict VARCHAR(8), reason_category VARCHAR(24), excess_pct FLOAT, note VARCHAR(512), created_at DATETIME, CONSTRAINT uq_daily_pick_review_date_symbol UNIQUE(date,symbol))'))
        conn.execute(text("INSERT INTO daily_pick_review (date,symbol,verdict,excess_pct,note) VALUES ('2026-10-08','600127','good',4.0,'legacy evidence')"))
        conn.execute(text('CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)'))
        conn.execute(text("INSERT INTO alembic_version VALUES ('e2c6a8f4b9d1')"))
    config = Config(str(Path(__file__).resolve().parents[1] / 'alembic.ini'))
    config.attributes['configure_logger'] = False
    with engine.begin() as conn:
        config.attributes['connection'] = conn
        command.upgrade(config, 'head')
        assert conn.execute(text('SELECT note,excess_pct,review_context,selection_version FROM daily_pick_review')).one() == ('legacy evidence', 4.0, None, None)
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar() == 'f2a7c9e4b6d8'
    col = next(c for c in inspect(engine).get_columns('daily_pick_review') if c['name'] == 'review_context')
    assert col['nullable'] is True
    with engine.begin() as conn:
        conn.execute(text("INSERT INTO daily_pick_review (date,symbol,selection_version,note) VALUES ('2026-10-08','600127','generationA','new A'),('2026-10-08','600127','generationB','new B')"))
    with engine.begin() as conn:
        config.attributes['connection'] = conn
        with pytest.raises(RuntimeError, match='preservation plan'):
            command.downgrade(config, 'e2c6a8f4b9d1')
        assert conn.execute(text('SELECT count(*) FROM daily_pick_review')).scalar() == 3
        assert conn.execute(text('SELECT version_num FROM alembic_version')).scalar() == 'f2a7c9e4b6d8'
    engine.dispose()
