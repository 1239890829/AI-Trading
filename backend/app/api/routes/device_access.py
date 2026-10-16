"""Fixed server-to-server pairing bridge; generic browser proxy cannot reach it."""
from __future__ import annotations

import ipaddress
import sqlite3
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import require_write_token
from app.core.config import settings
from app.core.device_access import AccessError, BROWSER_HEADER, ORIGIN_HEADER, SESSION_HEADER, get_device_store

router = APIRouter(prefix="/access", tags=["device-access"])


def _store_call(method: str, *args, **kwargs):
    if not settings.api_token:
        raise HTTPException(503, detail="device_access_requires_server_token")
    try:
        return getattr(get_device_store(), method)(*args, **kwargs)
    except AccessError as exc:
        raise HTTPException(exc.status, detail=exc.code) from exc
    except (OSError, sqlite3.Error):
        raise HTTPException(503, detail="device_access_unavailable") from None


def _identity(request: Request) -> tuple[str, str]:
    return request.headers.get(SESSION_HEADER, ""), request.headers.get(ORIGIN_HEADER, "")


class PairInput(BaseModel):
    label: str = Field(min_length=1, max_length=60)
    code: str = Field(default="", max_length=100)


class ManageInput(BaseModel):
    device_id: str = Field(pattern=r"^[0-9a-f]{16}$")
    action: Literal["approve", "revoke", "grant-maintenance", "end-maintenance"]


class CodeInput(BaseModel):
    recover: bool = False


@router.get("/status")
def status(request: Request):
    return _store_call("status", *_identity(request))


@router.post("/start", dependencies=[Depends(require_write_token)])
def start(body: PairInput, request: Request):
    token, state = _store_call("start", body.label, _identity(request)[1])
    return {"session_token": token, **state}


@router.post("/bootstrap", dependencies=[Depends(require_write_token)])
def bootstrap(body: PairInput, request: Request):
    token, state = _store_call("bootstrap", body.code, body.label, _identity(request)[1])
    return {"session_token": token, **state}


@router.post("/bootstrap-code", dependencies=[Depends(require_write_token)])
def bootstrap_code(body: CodeInput, request: Request):
    try:
        local = bool(request.client and ipaddress.ip_address(request.client.host).is_loopback)
    except ValueError:
        local = False
    if not local or any(request.headers.get(key) for key in (
        "x-ashare-access-bridge", "origin", BROWSER_HEADER, ORIGIN_HEADER, SESSION_HEADER,
    )):
        raise HTTPException(403, detail="local_operator_required")
    return {"code": _store_call("bootstrap_code", recover=body.recover), "expires_in": 300}


@router.get("/devices")
def devices(request: Request):
    return {"devices": _store_call("devices", *_identity(request))}


@router.post("/manage", dependencies=[Depends(require_write_token)])
def manage(body: ManageInput, request: Request):
    return _store_call("manage", *_identity(request), body.device_id, body.action)


@router.post("/logout", dependencies=[Depends(require_write_token)])
def logout(request: Request):
    _store_call("logout", *_identity(request))
    return {"ok": True}


@router.post("/ws-ticket", dependencies=[Depends(require_write_token)])
def ws_ticket(request: Request):
    return {"subprotocol": _store_call("ticket", *_identity(request)), "expires_in": 60}
