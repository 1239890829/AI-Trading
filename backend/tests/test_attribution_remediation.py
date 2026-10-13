"""Isolated regressions for source observations, response timing and qualified progress."""
from datetime import date
from types import SimpleNamespace as NS

import pytest

from app.events.extract import build_event
from app.schemas.market import LimitUpRecord


def test_same_day_reason_survives_source_switch_without_mutating_current_pool(monkeypatch):
    import asyncio
    async def check():
        from app.api.routes import market_pools as routes
        day = date(2026, 10, 13)
        rows = [[LimitUpRecord(symbol='002343', trade_date=day, reason='微短剧+影视IP', source='ths')],
                [LimitUpRecord(symbol='002343', trade_date=day, reason=None, source='eastmoney')]]
        async def pool(_day):
            return rows.pop(0)
        async def verify(*a, **kw):
            return None
        async def meta(*a, **kw):
            return {'source': 'test'}
        monkeypatch.setattr(routes, 'verify_pool_date', verify)
        monkeypatch.setattr(routes, 'dated_meta', meta)
        request = NS(app=NS(state=NS()))
        hub = NS(provider=NS(get_limit_up_pool=pool))
        first = await routes.limit_up(request, date_str=day.isoformat(), hub=hub)
        from app.core.ttl_cache import cache_on
        cache_on(request.app.state, 'market.limit_up', 60, maxsize=8).invalidate()
        second = await routes.limit_up(request, date_str=day.isoformat(), hub=hub)
        assert second['data']['pool'][0]['source'] == 'eastmoney'
        assert second['data']['pool'][0]['reason'] is None
        obs = second['data']['reason_observations']['002343'][0]
        assert obs['source'] == 'ths' and obs['reason'] == '微短剧+影视IP'
        assert obs['observed_at'] == first['data']['reason_observations']['002343'][0]['observed_at']
        obs['reason'] = 'caller mutation'
        hit = await routes.limit_up(request, date_str=day.isoformat(), hub=hub)
        assert hit['data']['reason_observations']['002343'][0]['reason'] == '微短剧+影视IP'
    asyncio.run(check())


def test_progress_disclaimer_is_not_new_positive_fact():
    result = build_event('慈文传媒AIGC平台仍处建设完善阶段 相关项目暂未形成收入',
        source='eastmoney', source_symbol='002343', theme_names=['AIGC概念'],
        summary='AIGC平台尚在建设完善中，尚未形成相关收入。相关业务进展及对公司生产经营与财务状况的影响，均存在不确定性。')
    assert result['certainty'] == 'done'
    assert result['directions'] and all(d['direction'] == 0 for d in result['directions'])


@pytest.mark.parametrize('title,summary', [
    ('公司签约重大订单', '另一研发项目尚未形成收入，进展仍存在不确定性。'),
    ('研究取得突破性进展', '该研究尚未形成销售收入。'),
])
def test_independent_positive_fact_is_preserved(title, summary):
    result = build_event(title, summary=summary, source_symbol='600001', theme_names=['公司'])
    assert any(d['target_type'] == 'symbol' and d['direction'] == 1 for d in result['directions'])


def test_disclosed_progress_is_not_suppressed_by_future_impact_uncertainty():
    result = build_event('公司披露本期研发取得进展', source_symbol='600001',
                         summary='本期研发取得进展，已经完成样机，未来财务影响存在不确定性。')
    assert result['directions'][0]['direction'] == 1
