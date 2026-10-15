"""Unified read projection keeps shadow accounts and factual denominators separate."""
from datetime import datetime

import pytest
from sqlalchemy import create_engine, event, select, func
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.watchlist import Base
from app.models.paper import PaperOrder, PaperAccount
from app.models.hunting_shadow import HuntingShadowAttempt
from app.picks.execution_validation import execution_report


@pytest.fixture
def sf():
    engine = create_engine('sqlite://', poolclass=StaticPool, connect_args={'check_same_thread': False})
    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def order(scope, *, status='filled', symbol='600127', at='2026-10-12T02:01:00', price=10):
    return PaperOrder(scope=scope, symbol=symbol, side='buy', price=10, quantity=100,
                      status=status, filled_price=price, fee=5, created_at=datetime.fromisoformat(at))


def test_accounts_separate_and_attempt_order_not_double_counted(sf):
    with sf() as db:
        a, b, main = order('shadow'), order('hunting_shadow', status='rejected', price=None), order('main')
        db.add_all([a,b,main]); db.flush()
        db.add(HuntingShadowAttempt(id='attempt',trade_date='2026-10-12',symbol='600127',decision_id='d',decision_version='v',snapshot_id='s',state='rejected',entry_order_id=b.id,reason='capital_slots_full',evidence='{}'))
        db.commit()
    log = {'day':'2026-10-12','executed_at':'2026-10-12T09:26:00+08:00','pick_date':'2026-10-11','bought':[{'symbol':'600127','status':'filled'}], 'skipped':[]}
    out = execution_report('2026-10-12','2026-10-13',session_factory=sf,log_reader=lambda day:log if day.endswith('12') else None)
    assert [a['orders'] for a in out['accounts']] == [1,1]
    assert [a['filled'] for a in out['accounts']] == [1,0]
    assert len(out['orders']) == 2 and len(out['attempts']) == 1
    assert out['attempts'][0]['order_state'] == 'rejected'
    assert out['runs'][0]['state'] == 'executed' and out['runs'][1]['state'] == 'unknown'
    assert 'win_rate' not in out and 'net_return_pct' not in out
    assert all(r['scope'] != 'main' for r in out['orders'])


def test_wrong_scope_missing_order_and_bad_fill_are_unknown_not_success(sf):
    with sf() as db:
        bad = order('hunting_shadow',price=0); main = order('main')
        db.add_all([bad,main]);db.flush()
        for key, oid in [('wrong',main.id),('missing',999),('bad',bad.id)]:
            db.add(HuntingShadowAttempt(id=key,trade_date='2026-10-12',symbol='600127',decision_id=key,decision_version='v',snapshot_id='s',state='filled',entry_order_id=oid,evidence='{}'))
        db.commit()
    out=execution_report('2026-10-12','2026-10-12',session_factory=sf,log_reader=lambda _:None)
    assert out['accounts'][1]['filled'] == 0 and out['accounts'][1]['unknown'] == 1
    assert all(a['order_state']=='unknown' for a in out['attempts'])
    assert all(a['issue'] for a in out['attempts'])


def test_beijing_boundaries_and_empty_read_do_not_create_account_or_write(sf):
    with sf() as db:
        db.add_all([order('shadow',at='2026-10-11T16:00:00'), order('shadow',at='2026-10-12T16:00:00')]);db.commit()
    def readonly(conn,cursor,statement,parameters,context,many):
        assert statement.lstrip().upper().startswith(('SELECT','PRAGMA'))
    engine=sf.kw['bind'];event.listen(engine,'before_cursor_execute',readonly)
    try:
        out=execution_report('2026-10-12','2026-10-12',session_factory=sf,log_reader=lambda _:None)
    finally:
        event.remove(engine,'before_cursor_execute',readonly)
    assert len(out['orders'])==1 and out['orders'][0]['submitted_at'].startswith('2026-10-12T00:00:00')
    with sf() as db: assert db.scalar(select(func.count()).select_from(PaperAccount))==0
    with pytest.raises(ValueError): execution_report('2025-01-01','2026-10-12',session_factory=sf)


def test_route_reader_runs_in_worker(sf,monkeypatch):
    import asyncio,threading
    from app.api.routes import selection_validation as route
    thread=threading.get_ident()
    def reader(start,end):
        assert thread != threading.get_ident()
        return execution_report(start,end,session_factory=sf,log_reader=lambda _:None)
    monkeypatch.setattr(route,'execution_report',reader)
    assert asyncio.run(route.execution(start='2026-10-12',end='2026-10-12'))['data']['orders']==[]


def test_compact_dates_normalize_and_untrusted_log_clock_cannot_prove_execution(sf):
    with sf() as db:
        db.add(order('shadow'));db.commit()
    out=execution_report('20261012','20261012',session_factory=sf,
        log_reader=lambda _: {'day':'2026-10-12','executed_at':'2026-10-13T09:26:00+08:00','bought':[]})
    assert out['start']=='2026-10-12' and len(out['orders'])==1
    assert out['runs'][0]['state']=='unknown'
    out=execution_report('2026-10-12','2026-10-12',session_factory=sf,
        log_reader=lambda _: {'day':'2026-10-12','skipped_reason':'not_trading_day'})
    assert out['runs'][0]['state']=='skipped'
