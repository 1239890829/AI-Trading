"""Public source observation identity shared by independent asset readers."""
from datetime import datetime

from pydantic import BaseModel


class AssetEvidence(BaseModel):
    endpoint: str
    params: dict[str, str | int]
    received_at: datetime
    payload_sha256: str
    receipt_id: str
    raw_available: bool = False
