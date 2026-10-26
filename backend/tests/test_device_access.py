"""Offline device boundaries; no market sources, real sessions or schedulers."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import stat

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect

from app.api.deps import require_api_token, require_write_token
from app.api.routes.device_access import router
from app.core import device_access as access
from app.core.config import settings
from app.core.device_policy import needs_maintenance
from app.websocket.routes import router as ws_router

ORIGIN = 'http://device.test:3000'
PHONE = 'http://phone.test:3000'


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(access, 'STORE_PATH', tmp_path / 'access.sqlite3')
    monkeypatch.setattr(settings, 'api_token', 'fixture-service')
    return access.get_device_store()


def owner(store):
    return store.bootstrap(store.bootstrap_code(), '电脑', ORIGIN)


def phone(store, admin):
    token, row = store.start('手机', PHONE)
    store.manage(admin, ORIGIN, row['id'], 'approve')
    return token, row


def test_pending_is_not_authorized_and_approval_does_not_grant_maintenance(store):
    admin, _ = owner(store)
    token, row = store.start('手机', PHONE)
    with pytest.raises(access.AccessError, match='device_pairing_required'):
        store.authorize(token, PHONE)
    store.manage(admin, ORIGIN, row['id'], 'approve')
    assert store.authorize(token, PHONE)['maintenance'] is False
    with pytest.raises(access.AccessError, match='maintenance_approval_required'):
        store.authorize(token, PHONE, maintenance=True)
    with pytest.raises(access.AccessError):
        store.authorize(token, ORIGIN)
    with pytest.raises(access.AccessError, match='owner_approval_required'):
        store.manage(token, PHONE, row['id'], 'grant-maintenance')


def test_permission_expiry_logout_and_revocation(store, monkeypatch):
    clock = 2_000_000_000.0
    monkeypatch.setattr(access.time, 'time', lambda: clock)
    admin, _ = owner(store)
    token, row = phone(store, admin)
    store.manage(admin, ORIGIN, row['id'], 'grant-maintenance')
    assert store.authorize(token, PHONE, maintenance=True)['maintenance']
    clock += access.MAINTENANCE_SECONDS
    with pytest.raises(access.AccessError):
        store.authorize(token, PHONE, maintenance=True)
    assert store.authorize(token, PHONE)['state'] == 'active'
    store.logout(token, PHONE)
    with pytest.raises(access.AccessError):
        store.authorize(token, PHONE)
    assert not store.ws_active(row['id'])


def test_pending_expiry_does_not_extend_with_polling(store, monkeypatch):
    clock = 2_000_000_000.0
    monkeypatch.setattr(access.time, 'time', lambda: clock)
    token, row = store.start('手机', PHONE)
    clock += 299
    assert store.status(token, PHONE)['expires_at'] == row['expires_at']
    clock += 1
    assert store.status(token, PHONE) == {'state': 'unpaired'}


def test_bootstrap_is_local_code_one_use_and_recovery_is_explicit(store):
    code = store.bootstrap_code()
    admin, _ = store.bootstrap(code, '电脑', ORIGIN)
    with pytest.raises(access.AccessError):
        store.bootstrap(code, '另一台', ORIGIN)
    with pytest.raises(access.AccessError, match='owner_already_paired'):
        store.bootstrap_code()
    mobile, row = phone(store, admin)
    ticket = store.ticket(mobile, PHONE)
    recovery = store.bootstrap_code(recover=True)
    assert store.ws_active(row['id'])  # Issuing is not claiming recovery.
    store.bootstrap(recovery, '新电脑', ORIGIN)
    with pytest.raises(access.AccessError):
        store.authorize(admin, ORIGIN)
    with pytest.raises(access.AccessError):
        store.consume_ticket(ticket, PHONE)
    assert not store.ws_active(row['id'])


def test_ticket_is_origin_bound_one_use_and_not_a_long_term_secret(store):
    admin, _ = owner(store)
    token, row = phone(store, admin)
    ticket = store.ticket(token, PHONE)
    with pytest.raises(access.AccessError):
        store.consume_ticket(ticket, ORIGIN)
    assert store.consume_ticket(ticket, PHONE) == row['id']
    with pytest.raises(access.AccessError):
        store.consume_ticket(ticket, PHONE)
    assert token not in ticket and settings.api_token not in ticket
    raw = store.path.read_bytes()
    assert admin.encode() not in raw and token.encode() not in raw and ticket.encode() not in raw
    assert stat.S_IMODE(store.path.stat().st_mode) == 0o600


def test_ticket_replay_concurrent_and_expiry(store, monkeypatch):
    clock = 2_000_000_000.0
    monkeypatch.setattr(access.time, 'time', lambda: clock)
    admin, row = owner(store)
    ticket = store.ticket(admin, ORIGIN)
    def consume():
        try:
            return store.consume_ticket(ticket, ORIGIN)
        except access.AccessError:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        values = list(pool.map(lambda _: consume(), range(2)))
    assert values.count(row['id']) == 1 and values.count(None) == 1
    ticket = store.ticket(admin, ORIGIN)
    clock += access.WS_TICKET_SECONDS
    with pytest.raises(access.AccessError):
        store.consume_ticket(ticket, ORIGIN)


def test_untrusted_queue_and_ticket_storage_are_bounded(store):
    for i in range(20):
        store.start(str(i), PHONE)
    with pytest.raises(access.AccessError, match='pairing_queue_full'):
        store.start('overflow', PHONE)
    admin, _ = owner(store)
    for _ in range(32):
        store.ticket(admin, ORIGIN)
    with pytest.raises(access.AccessError, match='ws_ticket_limit'):
        store.ticket(admin, ORIGIN)


@pytest.mark.parametrize('method,path,maintenance', [
    ('GET','/api/quotes/{symbol}',False), ('GET','/api/picks/today',False),
    ('GET','/api/notifications/diagnostics',False),
    ('GET','/api/picks/validation',False), ('GET','/api/picks/validation/execution',True),
    ('POST','/api/watchlist',False), ('DELETE','/api/watchlist/groups/{name}',False),
    ('PUT','/api/notifications/read-state',False), ('POST','/api/assistant/chat',False),
    ('GET','/api/system/llm-probe',True), ('GET','/api/agent/tasks',True),
    ('POST','/api/picks/generate',True), ('POST','/api/agent/params/change',True),
    ('GET','/api/new-unreviewed-endpoint',True), ('POST','/api/quotes',True),
    ('GET','/api/quotes/600127/secret',True), ('GET','/api/access/bootstrap-code',True),
    ('GET','/api/quotes/internal-export',True), ('GET','',True),
])
def test_default_deny_capability_policy(method,path,maintenance):
    assert needs_maintenance(method,path) is maintenance


class Hub:
    def __init__(self): self.queues = set()
    def subscribe(self, symbols, *, subscription_id=0):
        queue = asyncio.Queue()
        self.queues.add(queue)
        return queue
    def unsubscribe(self, queue): self.queues.discard(queue)
    def get_quotes(self, symbols): return []
    def next_seq(self): return 1


@pytest.fixture
def client(store):
    app = FastAPI()
    app.state.hub = Hub()
    app.include_router(router, prefix='/api', dependencies=[Depends(require_api_token)])
    app.include_router(ws_router)
    @app.get('/api/quotes', dependencies=[Depends(require_api_token)])
    def quote(): return {'data': []}
    @app.post('/api/agent/params/change', dependencies=[Depends(require_api_token), Depends(require_write_token)])
    def maintenance(): return {'ok': True}
    @app.get('/api/unknown-read', dependencies=[Depends(require_api_token)])
    def unknown(): return {'ok': True}
    @app.get('/api/quotes/internal-export', dependencies=[Depends(require_api_token)])
    def internal_export(): return {'internal': True}
    @app.get('/api/quotes/{symbol}', dependencies=[Depends(require_api_token)])
    def symbol_quote(symbol: str): return {'symbol': symbol}
    return TestClient(app, client=('127.0.0.1', 50000))


def headers(token, origin=ORIGIN):
    return {'X-API-Token': 'fixture-service', access.BROWSER_HEADER: '1', access.SESSION_HEADER: token, access.ORIGIN_HEADER: origin}


def test_actual_http_requires_server_and_device_then_maintenance(client, store):
    admin, row = owner(store)
    assert client.get('/api/quotes').status_code == 401
    forged = headers(admin); forged['X-API-Token'] = 'wrong'
    assert client.get('/api/quotes', headers=forged).status_code == 401
    assert client.get('/api/quotes', headers=headers('unknown')).status_code == 401
    assert client.get('/api/quotes', headers=headers(admin)).status_code == 200
    assert client.get('/api/unknown-read', headers=headers(admin)).status_code == 403
    assert client.get('/api/quotes/internal-export', headers=headers(admin)).status_code == 403
    assert client.get('/api/quotes/600127', headers=headers(admin)).status_code == 200
    assert client.post('/api/agent/params/change', headers=headers(admin)).status_code == 403
    store.manage(admin, ORIGIN, row['id'], 'grant-maintenance')
    assert client.post('/api/agent/params/change', headers=headers(admin)).status_code == 200
    assert client.get('/api/quotes', headers={'X-API-Token':'fixture-service','Origin':ORIGIN}).status_code == 401


@pytest.mark.parametrize('depth', [0, 1, 2])
def test_included_router_keeps_business_and_maintenance_permissions(store, depth):
    app = FastAPI()
    business = APIRouter()
    @business.get('/quotes/{symbol}')
    def quote(symbol: str): return {'symbol': symbol}
    @business.get('/quotes/internal-export')
    def internal_export(): return {'internal': True}
    @business.post('/watchlist')
    def watchlist(): return {'ok': True}
    @business.post('/picks/generate')
    def generate(): return {'ok': True}
    # Exact static routes must precede the existing dynamic quote route.
    business.routes[0], business.routes[1] = business.routes[1], business.routes[0]
    for _ in range(depth):
        parent = APIRouter()
        parent.include_router(business)
        business = parent
    app.include_router(business, prefix='/api', dependencies=[Depends(require_api_token)])
    app.include_router(business, prefix='/internal/api', dependencies=[Depends(require_api_token)])
    mounted = FastAPI()
    mounted.include_router(business, prefix='/api', dependencies=[Depends(require_api_token)])
    app.mount('/private', mounted)
    probe = TestClient(app)
    admin, _ = owner(store)
    mobile, _ = phone(store, admin)
    for token, origin in [(admin, ORIGIN), (mobile, PHONE)]:
        assert store.authorize(token, origin)['maintenance'] is False
        h = headers(token, origin)
        assert probe.get('/api/quotes/600127', headers=h).status_code == 200
        assert probe.post('/api/watchlist', headers=h).status_code == 200
        assert probe.get('/api/quotes/internal-export', headers=h).status_code == 403
        assert probe.get('/internal/api/quotes/600127', headers=h).status_code == 403
        assert probe.get('/private/api/quotes/600127', headers=h).status_code == 403
        assert probe.post('/api/picks/generate', headers=h).status_code == 403
    assert probe.get('/api/quotes/600127', headers=headers('unknown')).status_code == 401


@pytest.mark.parametrize('paired_phone', [False, True])
def test_real_app_business_routes_reach_consumer_without_maintenance(store, monkeypatch, paired_phone):
    """Exercise actual registration; stop after auth before any business consumer."""
    import re
    from app.main import app
    from app.core.device_policy import READ_PATHS, WRITE_PATHS

    async def stop_after_authorization(request: Request):
        await require_api_token(request)
        raise HTTPException(418, detail='authorized_before_consumer')

    monkeypatch.setattr(app, 'dependency_overrides', {
        **app.dependency_overrides, require_api_token: stop_after_authorization,
    })
    admin, _ = owner(store)
    token, origin = (phone(store, admin)[0], PHONE) if paired_phone else (admin, ORIGIN)
    operations = {('GET', path) for path in READ_PATHS} | set(WRITE_PATHS)
    contract = app.openapi()['paths']
    probe = TestClient(app)  # No lifespan, scheduler, source, model or business write.
    for method, template in sorted(operations):
        template = '/api' + template
        assert method.lower() in contract[template], (method, template)
        concrete = re.sub(r'\{[^}]+\}', '600127', template)
        response = probe.request(method, concrete, headers=headers(token, origin))
        assert response.status_code == 418, (method, template, response.json())


def test_missing_or_mismatched_route_context_does_not_inherit_business_access():
    from types import SimpleNamespace
    from app.core.device_policy import matched_route_template

    route = SimpleNamespace(path='/api/quotes')
    assert matched_route_template({'route': route}) == '/api/quotes'
    for scope in [
        {'path': '/api/quotes'},  # A user-controlled URL is not a route identity.
        {'fastapi': {'effective_route_context': SimpleNamespace(path='/api/quotes')}},
        {'route': route, 'fastapi': {'effective_route_context': SimpleNamespace(original_route=route)}},
        {'route': route, 'fastapi': {'effective_route_context': SimpleNamespace(original_route=object(), path='/api/quotes')}},
        {'route': route, 'root_path': 5},
    ]:
        assert needs_maintenance('GET', matched_route_template(scope)) is True


def test_bootstrap_cannot_be_issued_by_browser_or_non_loopback(client, store):
    admin, row = owner(store)
    store.manage(admin, ORIGIN, row['id'], 'grant-maintenance')
    assert client.post('/api/access/bootstrap-code', headers=headers(admin), json={'recover':True}).status_code == 403
    for header in ['x-ashare-access-bridge', access.ORIGIN_HEADER, access.SESSION_HEADER]:
        assert client.post('/api/access/bootstrap-code', headers={'X-API-Token':'fixture-service',header:'1'},json={}).status_code == 403
    remote = TestClient(client.app, client=('192.0.2.1', 50000))
    assert remote.post('/api/access/bootstrap-code',headers={'X-API-Token':'fixture-service'},json={}).status_code == 403
    assert client.post('/api/access/bootstrap-code',headers={'X-API-Token':'fixture-service'},json={'recover':True}).status_code == 200


def test_ws_ticket_refresh_replay_and_revocation_close(client, store, monkeypatch):
    import app.websocket.routes as ws
    monkeypatch.setattr(ws, 'WS_RECHECK_SECONDS', 0.02)
    admin, row = owner(store)
    ticket = store.ticket(admin, ORIGIN)
    with client.websocket_connect('/ws/quotes', subprotocols=[ticket], headers={'origin':ORIGIN}) as socket:
        assert socket.receive_json()['type'] == 'snapshot'
        store.logout(admin, ORIGIN)
        with pytest.raises(WebSocketDisconnect) as closed:
            socket.receive_json()
        assert closed.value.code == 1008
    assert not client.app.state.hub.queues
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect('/ws/quotes', subprotocols=[ticket], headers={'origin':ORIGIN}): pass
    admin, _ = owner(store)
    for _ in range(2):
        fresh = store.ticket(admin, ORIGIN)
        with client.websocket_connect('/ws/quotes', subprotocols=[fresh],headers={'origin':ORIGIN}) as socket:
            assert socket.receive_json()['type'] == 'snapshot'
    assert not client.app.state.hub.queues


def test_encoded_access_path_does_not_turn_maintenance_into_owner(client, store):
    admin, row = owner(store)
    token, mobile = phone(store, admin)
    store.manage(admin, ORIGIN, mobile['id'], 'grant-maintenance')
    response = client.post('/api/%61ccess/bootstrap-code', headers=headers(token, PHONE), json={'recover': True})
    assert response.status_code == 403
    assert store.authorize(admin, ORIGIN)['owner'] is True


def test_two_approvals_do_not_reextend_session_and_expired_owner_cannot_approve(store, monkeypatch):
    clock = 2_000_000_000.0
    monkeypatch.setattr(access.time,'time',lambda:clock)
    admin, _ = owner(store)
    token, row = phone(store,admin)
    expiry = store.status(token,PHONE)['expires_at']
    clock += 10
    with pytest.raises(access.AccessError):
        store.manage(admin,ORIGIN,row['id'],'approve')
    assert store.status(token,PHONE)['expires_at'] == expiry
    clock += access.SESSION_SECONDS
    with pytest.raises(access.AccessError):
        store.manage(admin,ORIGIN,row['id'],'grant-maintenance')
