"""Read-only selection validation; no live source calls or historical backfills."""
import asyncio
from datetime import timedelta
from fastapi import APIRouter, HTTPException, Query
from app.core.bjtime import beijing_today
from app.picks.validation_read import read_validation
from app.picks.execution_validation import execution_report

router = APIRouter(prefix='/picks/validation', tags=['selection-validation'])


@router.get('/execution')
async def execution(start: str | None = Query(None), end: str | None = Query(None)):
    today = beijing_today()
    try:
        data = await asyncio.to_thread(execution_report, start or (today-timedelta(days=29)).isoformat(), end or today.isoformat())
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {'data': data, 'meta': {'basis': 'internal_paper_execution_only'}}


@router.get('')
async def report(start: str | None = Query(None), end: str | None = Query(None),
                 horizon: str = Query('d1_close'), scope: str = Query('all'),
                 known_revision: str | None = Query(None, pattern=r'^[0-9a-f]{64}$')):
    today = beijing_today()
    try:
        data, revision = await asyncio.to_thread(read_validation, start or (today-timedelta(days=29)).isoformat(),
                                       end or today.isoformat(), horizon=horizon, scope=scope)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {'data': None if known_revision == revision else data,
            'meta': {'revision': revision, 'not_modified': known_revision == revision,
                     'version_identity': ['scope', 'method_version', 'feature_version'],
                     'basis': 'selection_observation_and_manual_fills_separate',
                                 'disclaimer': '不构成买卖建议；人工记录未经券商核验'}}


@router.get('/entries')
async def entries(symbol: str = Query(pattern=r'^\d{6}$'), days: int = Query(366, ge=1, le=366)):
    from app.core.db import get_session_factory
    from app.picks.validation import selection_rows
    today = beijing_today()
    def read():
        with get_session_factory()() as db:
            rows = selection_rows(db, (today-timedelta(days=days)).isoformat(), today.isoformat(), symbol=symbol)
        return [e for e in rows if e['state'] == 'recorded']
    try:
        return {'data': await asyncio.to_thread(read), 'meta': {'basis': 'original_visible_selections_only'}}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
