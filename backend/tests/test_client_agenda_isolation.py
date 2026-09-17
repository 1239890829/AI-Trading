"""Shared API tests must not generate agendas when the host crosses 15:45."""
import asyncio
from datetime import datetime

import pytest

from app.core.bjtime import BJ_TZ
from app.services import evolution


@pytest.fixture(scope="module", autouse=True)
def after_close_agenda_spy():
    calls = []
    now = datetime(2099, 1, 5, 16, 0, tzinfo=BJ_TZ)

    async def trading_days(*args, **kwargs):
        return [now.date()]

    async def generate(*args, **kwargs):
        calls.append("agenda")
        return {"status": "failed", "items": []}

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(evolution, "beijing_now", lambda: now)
        patch.setattr(evolution.tc, "trading_days", trading_days)
        patch.setattr(evolution, "get_agenda", lambda *args: None)
        patch.setattr(evolution, "run_evolution_now", generate)
        yield calls
        assert calls == []


def test_shared_client_does_not_generate_background_agenda(client, after_close_agenda_spy):
    client.portal.call(asyncio.sleep, 0)
    assert after_close_agenda_spy == []
