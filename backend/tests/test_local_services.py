"""Local launchers use isolated configuration and never probe a real service."""
from __future__ import annotations

import importlib.util
import base64
import json
import os
from pathlib import Path
import selectors
import shutil
import stat
import subprocess
import sys
from types import SimpleNamespace
import urllib.error

import pytest


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/local-services.py"


@pytest.fixture
def launcher():
    spec = importlib.util.spec_from_file_location("local_services_test_target", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend/.env").write_text(
        'ASHARE_AUTH_MODE=shared\nASHARE_API_TOKEN="fixture=a=b"\n', encoding="utf-8"
    )
    return tmp_path


def test_frontend_receives_only_selected_server_credential(launcher, project):
    config = launcher.load_auth(project, {})
    env = launcher.frontend_environment(config, {
        "PATH": "/usr/bin", "HOME": "/fixture-home", "ASHARE_THS_API_KEY": "supplier-secret",
        "OPENAI_API_KEY": "model-secret", "NODE_OPTIONS": "--require=untrusted.js",
    }, dev=False)
    assert env == {
        "PATH": "/usr/bin", "HOME": "/fixture-home", "ASHARE_API_TOKEN": "fixture=a=b",
        "BACKEND_ORIGIN": "http://127.0.0.1:8000", "NODE_ENV": "production",
    }


@pytest.mark.parametrize("contents,env,code", [
    ("ASHARE_AUTH_MODE=shared\n", {}, "shared_token_missing"),
    ("ASHARE_AUTH_MODE=share\n", {}, "auth_mode_invalid"),
    ('ASHARE_API_TOKEN="${OTHER}"\n', {}, "auth_interpolation_not_supported"),
    ('ASHARE_API_TOKEN="abc\nxyz"\n', {}, "auth_token_invalid_header"),
    ("ASHARE_API_TOKEN=one\nASHARE_API_TOKEN=two\n", {}, "auth_field_duplicated"),
    ('ASHARE_API_TOKEN="unclosed\n', {}, "dotenv_parse_failed"),
    ("ASHARE_API_TOKEN=one\n", {"ASHARE_API_TOKEN": "two"}, "auth_environment_conflict"),
    ("ASHARE_API_TOKEN=one\n", {"ASHARE_API_TOKEN": ""}, "auth_environment_conflict"),
    ("ASHARE_AUTH_MODE=shared\n", {"ASHARE_AUTH_MODE": "local"}, "auth_environment_conflict"),
])
def test_invalid_auth_refuses_without_disclosing_values(launcher, project, contents, env, code):
    (project / "backend/.env").write_text(contents, encoding="utf-8")
    with pytest.raises(launcher.ServiceError) as error:
        launcher.load_auth(project, env)
    assert str(error.value) == code


def test_dotenv_quotes_equals_export_comments_and_equal_environment(launcher, project):
    (project / "backend/.env").write_text(
        'export ASHARE_AUTH_MODE="shared" # posture\n'
        'ASHARE_API_TOKEN=\'fixture=a=b#c\' # comment\n'
        'ASHARE_THS_API_KEY=${NOT_READ_OR_EXPORTED}\n', encoding="utf-8"
    )
    config = launcher.load_auth(project, {"ASHARE_API_TOKEN": "fixture=a=b#c"})
    assert config.mode == "shared" and config.token == "fixture=a=b#c"
    assert config.token not in repr(config)


def test_local_without_file_is_valid_but_shared_without_token_is_not(launcher, tmp_path):
    config = launcher.load_auth(tmp_path, {})
    assert (config.mode, config.token) == ("local", "")
    with pytest.raises(launcher.ServiceError, match="shared_token_missing"):
        launcher.load_auth(tmp_path, {"ASHARE_AUTH_MODE": "shared"})


def test_auth_key_case_matches_backend_case_insensitive_settings(launcher, project):
    (project / "backend/.env").write_text(
        "ashare_auth_mode=shared\nashare_api_token=fixture=a=b\n", encoding="utf-8"
    )
    config = launcher.load_auth(project, {"ashare_api_token": "fixture=a=b"})
    assert config.mode == "shared" and config.token == "fixture=a=b"
    with pytest.raises(launcher.ServiceError, match="auth_environment_conflict"):
        launcher.load_auth(project, {"ASHARE_API_TOKEN": "fixture=a=b", "ashare_api_token": "other"})


def test_frontend_rejects_different_backend_origin(launcher, project):
    with pytest.raises(launcher.ServiceError, match="backend_origin_conflict"):
        launcher.frontend_environment(launcher.load_auth(project, {}), {
            "BACKEND_ORIGIN": "http://another-backend:8000"
        }, dev=False)


class Executed(BaseException):
    pass


def fake_runtime(launcher, project, monkeypatch, *, listening=False):
    web = project / "apps/web"
    (web / "node_modules/next/dist/bin").mkdir(parents=True)
    (web / "node_modules/next/dist/bin/next").write_text("// fixture", encoding="utf-8")
    (web / ".next").mkdir()
    (web / ".next/BUILD_ID").write_text("fixture-build", encoding="utf-8")
    monkeypatch.setattr(launcher.shutil, "which", lambda *_args, **_kwargs: "/fixture/node")
    monkeypatch.setattr(launcher.importlib.util, "find_spec", lambda _name: object())
    monkeypatch.setattr(launcher, "port_is_listening", lambda *_args: listening)
    seen = {}
    monkeypatch.setattr(launcher.os, "chdir", lambda path: seen.update(cwd=path))

    def execute(binary, argv, env):
        seen.update(binary=binary, argv=argv, env=env)
        raise Executed

    monkeypatch.setattr(launcher.os, "execve", execute)
    return seen


@pytest.mark.parametrize("service,dev", [("backend", False), ("frontend", False), ("frontend", True)])
def test_foreground_launch_uses_fixed_ports_without_build_or_reload(launcher, project, monkeypatch, service, dev):
    seen = fake_runtime(launcher, project, monkeypatch)
    with pytest.raises(Executed):
        launcher.launch(project, service, dev=dev, environ={"PATH": "/fixture"})
    if service == "backend":
        assert seen["cwd"] == project / "backend"
        assert seen["argv"] == [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]
        assert seen["env"]["ASHARE_AUTH_MODE"] == "shared"
    else:
        assert seen["cwd"] == project / "apps/web"
        assert seen["argv"][2:] == ["dev" if dev else "start", "--hostname", "0.0.0.0", "--port", "3000"]
        assert seen["env"]["NODE_ENV"] == ("development" if dev else "production")
    assert seen["env"]["ASHARE_API_TOKEN"] == "fixture=a=b"


def test_local_unprotected_frontend_stays_on_loopback(launcher, project, monkeypatch):
    seen = fake_runtime(launcher, project, monkeypatch)
    (project / "backend/.env").write_text("ASHARE_AUTH_MODE=local\n", encoding="utf-8")
    with pytest.raises(Executed):
        launcher.launch(project, "frontend", dev=False, environ={})
    assert seen["argv"][seen["argv"].index("--hostname") + 1] == "127.0.0.1"


def test_backend_does_not_inherit_reload_worker_or_python_injection_switches(launcher, project, monkeypatch):
    seen = fake_runtime(launcher, project, monkeypatch)
    unsafe = {key: "injected" for key in (
        "UVICORN_RELOAD", "UVICORN_WORKERS", "WEB_CONCURRENCY", "PYTHONPATH", "PYTHONHOME",
        "PYTHONSTARTUP", "PYTHONINSPECT",
    )}
    with pytest.raises(Executed):
        launcher.launch(project, "backend", dev=False, environ={**unsafe, "ASHARE_NEWS_MODEL": "existing"})
    assert all(key not in seen["env"] for key in unsafe if key != "PYTHONPATH")
    assert seen["env"]["PYTHONPATH"] == ""
    assert seen["env"]["ASHARE_NEWS_MODEL"] == "existing"


def test_occupied_port_never_executes_and_releases_lease(launcher, project, monkeypatch):
    seen = fake_runtime(launcher, project, monkeypatch, listening=True)
    with pytest.raises(launcher.ServiceError, match="port_already_listening"):
        launcher.launch(project, "frontend", dev=False, environ={})
    assert seen == {}
    fd = launcher.acquire_port_lock(project, 3000)
    os.close(fd)


def test_missing_production_build_refuses_before_launch(launcher, project, monkeypatch):
    seen = fake_runtime(launcher, project, monkeypatch)
    (project / "apps/web/.next/BUILD_ID").unlink()
    with pytest.raises(launcher.ServiceError, match="production_build_missing"):
        launcher.launch(project, "frontend", dev=False, environ={})
    assert seen == {}
    assert not (project / "artifacts").exists()


@pytest.mark.parametrize("service,code", [("frontend", "next_dependency_missing"), ("backend", "uvicorn_dependency_missing")])
def test_missing_dependencies_do_not_install_or_execute(launcher, project, monkeypatch, service, code):
    seen = fake_runtime(launcher, project, monkeypatch)
    monkeypatch.setattr(launcher.shutil, "which", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(launcher.importlib.util, "find_spec", lambda _name: None)
    with pytest.raises(launcher.ServiceError, match=code):
        launcher.launch(project, service, dev=False, environ={})
    assert seen == {}


def test_exec_failure_releases_lock_and_error_log_has_no_raw_exception(launcher, project, monkeypatch, capsys):
    fake_runtime(launcher, project, monkeypatch)

    def fail(*_args):
        raise OSError("fixture=a=b must never be printed")

    monkeypatch.setattr(launcher.os, "execve", fail)
    monkeypatch.setattr(launcher, "ROOT", project)
    monkeypatch.setattr(launcher.os, "environ", {})
    assert launcher.main(["frontend"]) == 1
    assert capsys.readouterr().err == "local_services: operation_failed\n"
    fd = launcher.acquire_port_lock(project, 3000)
    os.close(fd)


def test_port_inspection_is_listener_only_and_unknown_is_not_free(launcher, monkeypatch):
    seen = []
    monkeypatch.setattr(launcher.shutil, "which", lambda *_args, **_kwargs: "/fixture/lsof")

    def run(argv, **kwargs):
        seen.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout="123\n", stderr="")

    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.port_is_listening(3000, {"PATH": "/fixture", "OPENAI_API_KEY": "secret"})
    assert seen[0][0][-2:] == ["-iTCP:3000", "-sTCP:LISTEN"]
    assert "OPENAI_API_KEY" not in seen[0][1]["env"]
    monkeypatch.setattr(launcher.subprocess, "run", lambda *_args, **_kwargs: SimpleNamespace(
        returncode=1, stdout="", stderr="permission failed"
    ))
    with pytest.raises(launcher.ServiceError, match="listener_check_failed"):
        launcher.port_is_listening(3000, {})


def test_lock_survives_exec_blocks_concurrent_launcher_and_releases_on_exit(launcher, project):
    """The real OS lock must survive exec, not just a mocked Popen/lease object."""
    loader = (
        "import importlib.util,sys,os; "
        "s=importlib.util.spec_from_file_location('local_services_child',sys.argv[1]); "
        "m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); "
        "fd=m.acquire_port_lock(m.Path(sys.argv[2]),3000); "
        "os.execve(sys.executable,[sys.executable,'-c',"
        "\"import sys; print('ready',flush=True); sys.stdin.read(1)\"],{'PATH':os.defpath})"
    )
    with subprocess.Popen(
        [sys.executable, "-c", loader, str(SCRIPT), str(project)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={"PATH": os.defpath, "PYTHONPATH": ""},
    ) as owner:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(owner.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=5), "fixture lock owner did not become ready"
                assert owner.stdout.readline().strip() == "ready"
            lock_path = project / "artifacts/local-services/port-3000.lock"
            assert stat.S_IMODE(lock_path.stat().st_mode) == 0o600
            with pytest.raises(launcher.ServiceError, match="launcher_already_running"):
                launcher.acquire_port_lock(project, 3000)
            owner.communicate(input="q", timeout=5)
            assert owner.returncode == 0
        finally:
            if owner.poll() is None:
                owner.kill()
                owner.communicate(timeout=5)
    fd = launcher.acquire_port_lock(project, 3000)
    os.close(fd)


def test_lock_rejects_symlink_without_touching_target(launcher, project, tmp_path):
    target = tmp_path / "untouched"
    target.mkdir()
    (project / "artifacts").symlink_to(target, target_is_directory=True)
    with pytest.raises(launcher.ServiceError, match="lock_directory_symlink"):
        launcher.acquire_port_lock(project, 3000)
    assert list(target.iterdir()) == []


def test_port_lock_survives_real_node_exec_without_loading_next(launcher, tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node is unavailable; actual frontend exec lock is unverified")
    loader = (
        "import importlib.util,sys,os; "
        "s=importlib.util.spec_from_file_location('node_lock_child',sys.argv[1]); "
        "m=importlib.util.module_from_spec(s); sys.modules[s.name]=m; s.loader.exec_module(m); "
        "fd=m.acquire_port_lock(m.Path(sys.argv[2]),3000); "
        "os.execve(sys.argv[3],[sys.argv[3],'-e',"
        "\"console.log('ready'); process.stdin.once('data',()=>process.exit(0)); process.stdin.resume();\"],"
        "{'PATH':os.defpath})"
    )
    with subprocess.Popen(
        [sys.executable, "-c", loader, str(SCRIPT), str(tmp_path), node],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        env={"PATH": os.defpath, "PYTHONPATH": ""},
    ) as owner:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(owner.stdout, selectors.EVENT_READ)
                assert selector.select(timeout=5), "isolated Node lock owner did not become ready"
                assert owner.stdout.readline().strip() == "ready"
            with pytest.raises(launcher.ServiceError, match="launcher_already_running"):
                launcher.acquire_port_lock(tmp_path, 3000)
            owner.communicate(input="q", timeout=5)
            assert owner.returncode == 0
        finally:
            if owner.poll() is None:
                owner.kill()
                owner.communicate(timeout=5)
    fd = launcher.acquire_port_lock(tmp_path, 3000)
    os.close(fd)


def configure_checks(launcher, monkeypatch, *, token="fixture=a=b", change=None):
    calls = []
    expected = "ashare-token." + base64.urlsafe_b64encode(token.encode()).decode().rstrip("=") if token else None

    def read(url, headers):
        calls.append((url, dict(headers)))
        if change:
            replacement = change(url, headers)
            if replacement is not None:
                return replacement
        if url.endswith("/api/health"):
            return 200, {"status": "degraded"}, ""
        if url == f"{launcher.BACKEND_ORIGIN}/api/quotes" and token and not headers:
            return 401, None, ""
        if url.endswith("/api/ws-credential"):
            return 200, {"subprotocol": expected}, "no-store"
        return 200, {"data": []}, ""

    monkeypatch.setattr(launcher, "read_json", read)
    monkeypatch.setattr(launcher, "verify_service_owners", lambda *_args: None)
    return calls


@pytest.mark.parametrize("token", ["fixture=a=b", ""])
def test_check_accepts_empty_cache_without_claiming_market_or_ws_health(launcher, monkeypatch, token):
    calls = configure_checks(launcher, monkeypatch, token=token)
    result = launcher.check(launcher.AuthConfig("local", token))
    assert result["http_available"] is True and result["quote_rows"] == 0
    assert result["actual_ws"] == "not_verified_use_existing_browser"
    assert result["market_freshness"] == "not_verified"
    assert all(url in launcher.CHECK_URLS for url, _ in calls)
    assert len(calls) == (5 if token else 4)
    assert all(headers == {} for url, headers in calls if url.startswith(launcher.FRONTEND_ORIGIN))
    assert "fixture=a=b" not in json.dumps(result)


@pytest.mark.parametrize("failure,code", [
    ("proxy", "frontend_proxy_auth_failed"),
    ("credential", "frontend_ws_credential_mismatch"),
    ("cache", "frontend_ws_credential_mismatch"),
    ("backend_token", "backend_authenticated_read_failed"),
    ("unprotected", "backend_auth_posture_mismatch"),
    ("health", "backend_health_unavailable"),
])
def test_failed_auth_never_reports_ready_or_leaks_secret(launcher, project, monkeypatch, capsys, failure, code):
    def changed(url, headers):
        if failure == "proxy" and "/backend/api/quotes" in url:
            return 401, {"detail": "fixture=a=b"}, ""
        if failure == "credential" and url.endswith("/api/ws-credential"):
            return 200, {"subprotocol": "ashare-token.wrong-secret"}, "no-store"
        if failure == "cache" and url.endswith("/api/ws-credential"):
            return 200, {"subprotocol": "ashare-token.Zml4dHVyZT1hPWI"}, "public"
        if failure == "backend_token" and headers:
            return 401, None, ""
        if failure == "unprotected" and url == f"{launcher.BACKEND_ORIGIN}/api/quotes" and not headers:
            return 200, {"data": []}, ""
        if failure == "health" and url.endswith("/api/health"):
            return 503, {"detail": "fixture=a=b"}, ""
        return None

    configure_checks(launcher, monkeypatch, change=changed)
    monkeypatch.setattr(launcher, "ROOT", project)
    monkeypatch.setattr(launcher.os, "environ", {})
    assert launcher.main(["check"]) == 1
    output = capsys.readouterr()
    assert output.out == "" and output.err == f"local_services: {code}\n"


def test_http_probe_never_uses_environment_proxy_or_follows_redirects(launcher, monkeypatch):
    seen = {}

    def build(*handlers):
        seen["handlers"] = handlers

        def open_request(request, timeout):
            seen.update(url=request.full_url, headers=dict(request.header_items()), timeout=timeout)
            raise urllib.error.HTTPError(request.full_url, 302, "do-not-log", {}, None)

        return SimpleNamespace(open=open_request)

    monkeypatch.setattr(launcher.urllib.request, "build_opener", build)
    status, body, _ = launcher.read_json(f"{launcher.BACKEND_ORIGIN}/api/quotes", {"X-API-Token": "secret"})
    assert status == 302 and body is None
    assert seen["handlers"][0].proxies == {}
    assert seen["handlers"][1].redirect_request(None, None, 302, "", {}, "http://outside.invalid") is None
    with pytest.raises(launcher.ServiceError, match="check_url_forbidden"):
        launcher.read_json("https://outside.invalid", {})


@pytest.mark.parametrize("change,code", [
    ("cwd", "service_owner_mismatch"),
    ("uid", "service_owner_mismatch"),
    ("multiple", "service_listener_not_unique"),
    ("missing", "service_listener_not_unique"),
    ("reload", "service_command_mismatch"),
    ("foreign_app", "service_command_mismatch"),
    ("frontend", "service_command_mismatch"),
    ("changed", "service_listener_changed"),
])
def test_unknown_listener_gets_zero_http_and_no_credential(launcher, project, monkeypatch, capsys, change, code):
    reads = []
    count = {}

    def listeners(port, _env):
        count[port] = count.get(port, 0) + 1
        if port == 8000 and change == "multiple":
            return (10, 11)
        if port == 8000 and change == "missing":
            return ()
        if port == 8000 and change == "changed" and count[port] > 1:
            return (11,)
        return (10 if port == 8000 else 20,)

    def identity(pid, _env):
        uid = os.getuid() + int(change == "uid")
        if pid == 10:
            cwd = str(project / ("foreign" if change == "cwd" else "backend"))
            command = "/fixture/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000"
            if change == "reload":
                command += " --reload"
            if change == "foreign_app":
                command = "/fixture/python -m uvicorn foreign:app --port 8000 secret=fixture=a=b"
        else:
            cwd = str(project / "apps/web")
            command = "node foreign.js fixture=a=b" if change == "frontend" else "next-server (v16.3.8)"
        return uid, cwd, command

    monkeypatch.setattr(launcher, "listener_pids", listeners)
    monkeypatch.setattr(launcher, "process_identity", identity)
    monkeypatch.setattr(launcher, "ROOT", project)
    monkeypatch.setattr(launcher.os, "environ", {})
    monkeypatch.setattr(launcher, "read_json", lambda *args: reads.append(args))
    assert launcher.main(["check"]) == 1
    assert reads == []
    output = capsys.readouterr()
    assert output.out == "" and output.err == f"local_services: {code}\n"


def test_owner_inspection_uses_only_uid_cwd_command_not_process_environment(launcher, project, monkeypatch):
    calls = []
    monkeypatch.setattr(launcher.shutil, "which", lambda name, **_kwargs: f"/fixture/{name}")

    def run(argv, **kwargs):
        calls.append(argv)
        text = f"{os.getuid()} /fixture/python -m uvicorn app.main:app --port 8000\n" if "ps" in argv[0] else f"p10\nn{project / 'backend'}\n"
        return SimpleNamespace(returncode=0, stdout=text, stderr="")

    monkeypatch.setattr(launcher.subprocess, "run", run)
    assert launcher.process_identity(10, {})[:2] == (os.getuid(), str(project / "backend"))
    assert calls == [
        ["/fixture/ps", "-p", "10", "-o", "uid=", "-o", "command="],
        ["/fixture/lsof", "-a", "-p", "10", "-d", "cwd", "-Fn"],
    ]


@pytest.mark.parametrize("command,expected", [
    ("/fixture/python -m uvicorn app.main:app --host 127.0.0.1 --port 8000", True),
    ("/fixture/python /fixture/uvicorn app.main:app --port 8000", True),
    ("/fixture/uvicorn app.main:app --host=127.0.0.1 --port=8000 --workers=1", True),
    ("/fixture/uvicorn app.main:app --workers 2", False),
    ("/fixture/uvicorn app.main:app "
     "--reload=true", False),
    ("/fixture/uvicorn app.main:app --host 0.0.0.0", False),
])
def test_backend_owner_command_contract(launcher, command, expected):
    assert launcher.backend_command_matches(command) is expected
