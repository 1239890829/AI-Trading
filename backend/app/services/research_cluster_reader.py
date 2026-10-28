"""Freeze an actually observed projection; never read or modify old decisions."""
from __future__ import annotations

import asyncio

from app.core.bjtime import beijing_now
from app.services.research_clusters import build_research_clusters
from app.services.research_cluster_storage import publish_research_clusters


async def freeze_research_clusters(records: list[dict], *, trade_date: str, resolution: dict) -> dict:
    observed_at = beijing_now().isoformat()

    def freeze():
        payload = build_research_clusters(
            records, trade_date=trade_date, observed_at=observed_at, resolution=resolution,
        )
        if payload["state"] == "unavailable":
            return payload
        return publish_research_clusters(payload)

    return await asyncio.to_thread(freeze)
