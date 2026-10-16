#!/usr/bin/env python3
"""Launch one local service in the foreground, or check fixed HTTP auth paths.

Run with the backend virtualenv Python. This does not install, build, restart,
kill processes, open a WebSocket, or verify market-data freshness.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import stat
import subprocess
import sys
import urllib.error
import urllib.request


ROOT = Path(__file__).resolve().parents[1]
BACKEND_ORIGIN = "http://127.0.0.1:8000"
FRONTEND_ORIGIN = "http://127.0.0.1:3000"
AUTH_KEYS = ("ASHARE_AUTH_MODE", "ASHARE_API_TOKEN")
SYSTEM_ENV = frozenset({
    "PATH", "HOME", "TMPDIR", "TMP", "TEMP", "LANG", "LC_ALL", "LC_CTYPE", "TZ",
    "TERM", "COLORTERM", "NO_COLOR", "FORCE_COLOR", "SSL_CERT_FILE", "SSL_CERT_DIR",
})
CHECK_URLS = frozenset({
    f"{BACKEND_ORIGIN}/api/health", f"{BACKEND_ORIGIN}/api/quotes",
    f"{FRONTEND_ORIGIN}/backend/api/quotes", f"{FRONTEND_ORIGIN}/api/ws-credential",
    f"{FRONTEND_ORIGIN}/api/access/status",
})


class ServiceError(Exception):
    """A stable, non-secret diagnostic code. Never attach raw exception text."""


@dataclass(frozen=True)
class AuthConfig:
    mode: str
    token: str = field(repr=False)


def load_auth(root: Path, environ: dict[str, str]) -> AuthConfig:
    """Read only auth fields with dotenv quoting, without shell/interpolation."""
    try:
        from dotenv.parser import parse_stream
    except ImportError:
        raise ServiceError("dotenv_dependency_missing") from None
    selected: dict[str, str] = {}
    path = root / "backend/.env"
    try:
        with path.open(encoding="utf-8") as stream:
            for binding in parse_stream(stream):
                if binding.error:
                    raise ServiceError("dotenv_parse_failed")
                key = (binding.key or "").upper()
                if key in AUTH_KEYS:
                    if key in selected:
                        raise ServiceError("auth_field_duplicated")
                    selected[key] = binding.value or ""
    except FileNotFoundError:
        pass  # local + no token remains the existing zero-configuration mode.
    except (OSError, UnicodeError):
        raise ServiceError("dotenv_unreadable") from None
    for name, value in environ.items():
        key = name.upper()
        if key not in AUTH_KEYS:
            continue
        compare = str.lower if key == "ASHARE_AUTH_MODE" else str
        if key in selected and compare(value) != compare(selected[key]):
            raise ServiceError("auth_environment_conflict")
        selected[key] = value
    if any("${" in value for value in selected.values()):
        raise ServiceError("auth_interpolation_not_supported")
    mode = selected.get("ASHARE_AUTH_MODE", "local").lower()
    token = selected.get("ASHARE_API_TOKEN", "")
    if mode not in {"local", "shared"}:
        raise ServiceError("auth_mode_invalid")
    if token and any(ord(char) < 33 or ord(char) > 126 for char in token):
        raise ServiceError("auth_token_invalid_header")
    if mode == "shared" and not token:
        raise ServiceError("shared_token_missing")
    return AuthConfig(mode, token)


def frontend_environment(config: AuthConfig, environ: dict[str, str], *, dev: bool) -> dict[str, str]:
    if not config.token:
        raise ServiceError("device_access_requires_server_token")
    origin = environ.get("BACKEND_ORIGIN", BACKEND_ORIGIN)
    if origin != BACKEND_ORIGIN:
        raise ServiceError("backend_origin_conflict")
    return {
        **{key: value for key, value in environ.items() if key in SYSTEM_ENV},
        "ASHARE_API_TOKEN": config.token,
        "BACKEND_ORIGIN": BACKEND_ORIGIN,
        "NODE_ENV": "development" if dev else "production",
    }


def acquire_port_lock(root: Path, port: int) -> int:
    """Keep a same-checkout launch lease across exec; never unlink an active lock."""
    directory = root
    for name in ("artifacts", "local-services"):
        directory = directory / name
        if directory.is_symlink():
            raise ServiceError("lock_directory_symlink")
        directory.mkdir(mode=0o700, exist_ok=True)
    fd = os.open(directory / f"port-{port}.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ServiceError("lock_file_unsafe")
        os.fchmod(fd, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ServiceError("launcher_already_running") from None
        os.set_inheritable(fd, True)
        return fd
    except BaseException:
        os.close(fd)
        raise


def listener_pids(port: int, environ: dict[str, str]) -> tuple[int, ...]:
    lsof = shutil.which("lsof", path=environ.get("PATH", os.defpath))
    if not lsof:
        raise ServiceError("lsof_dependency_missing")
    try:
        result = subprocess.run(
            [lsof, "-nP", "-t", f"-iTCP:{port}", "-sTCP:LISTEN"],
            capture_output=True, text=True, timeout=5,
            env={key: value for key, value in environ.items() if key in SYSTEM_ENV},
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ServiceError("listener_check_failed") from None
    pids = result.stdout.split()
    if result.stderr.strip() or result.returncode not in {0, 1} or any(not pid.isdigit() for pid in pids):
        raise ServiceError("listener_check_failed")
    if result.returncode == 0 and not pids or result.returncode == 1 and pids:
        raise ServiceError("listener_check_failed")
    return tuple(sorted({int(pid) for pid in pids}))


def port_is_listening(port: int, environ: dict[str, str]) -> bool:
    return bool(listener_pids(port, environ))


def process_identity(pid: int, environ: dict[str, str]) -> tuple[int, str, str]:
    """Observe uid/cwd/command without reading process environments or logging args."""
    path = environ.get("PATH", os.defpath)
    ps, lsof = shutil.which("ps", path=path), shutil.which("lsof", path=path)
    if not ps or not lsof:
        raise ServiceError("owner_inspection_dependency_missing")
    env = {key: value for key, value in environ.items() if key in SYSTEM_ENV}
    try:
        proc = subprocess.run([ps, "-p", str(pid), "-o", "uid=", "-o", "command="],
                              capture_output=True, text=True, timeout=5, env=env)
        cwd = subprocess.run([lsof, "-a", "-p", str(pid), "-d", "cwd", "-Fn"],
                             capture_output=True, text=True, timeout=5, env=env)
    except (OSError, subprocess.TimeoutExpired):
        raise ServiceError("service_owner_unknown") from None
    fields = proc.stdout.strip().split(maxsplit=1)
    directories = [line[1:] for line in cwd.stdout.splitlines() if line.startswith("n")]
    if (proc.returncode != 0 or cwd.returncode != 0 or proc.stderr.strip() or cwd.stderr.strip()
            or len(fields) != 2 or not fields[0].isdigit() or len(directories) != 1):
        raise ServiceError("service_owner_unknown")
    return int(fields[0]), directories[0], fields[1]


def backend_command_matches(command: str) -> bool:
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    if not argv:
        return False
    first = Path(argv[0]).name.lower()
    if first.startswith("python") and argv[1:3] == ["-m", "uvicorn"]:
        argv = argv[3:]
    elif first.startswith("python") and len(argv) > 1 and Path(argv[1]).name == "uvicorn":
        argv = argv[2:]
    elif first == "uvicorn":
        argv = argv[1:]
    else:
        return False
    if not argv or argv[0] != "app.main:app":
        return False
    expected = {"--host": "127.0.0.1", "--port": "8000", "--workers": "1"}
    seen = set()
    remaining = iter(argv[1:])
    for option in remaining:
        key, equal, value = option.partition("=")
        if not equal:
            value = next(remaining, None)
        if key in seen or key not in expected or value != expected[key]:
            return False
        seen.add(key)
    return True


def frontend_command_matches(command: str, root: Path) -> bool:
    if re.fullmatch(r"next-server \(v[0-9][0-9A-Za-z.+-]*\)", command):
        return True
    try:
        argv = shlex.split(command)
    except ValueError:
        return False
    entry = root / "apps/web/node_modules/next/dist/bin/next"
    if len(argv) < 3 or Path(argv[0]).name != "node" or Path(argv[1]) != entry or argv[2] not in {"dev", "start"}:
        return False
    return argv[3:] in [
        ["--hostname", host, "--port", "3000"] for host in ("127.0.0.1", "0.0.0.0")
    ]


def verify_service_owners(root: Path, environ: dict[str, str]) -> None:
    """Reject unknown listeners before sending credentials, without killing any PID."""
    observed = {}
    for port, relative in ((8000, "backend"), (3000, "apps/web")):
        pids = listener_pids(port, environ)
        if len(pids) != 1:
            raise ServiceError("service_listener_not_unique")
        observed[port] = pids
        uid, cwd, command = process_identity(pids[0], environ)
        if uid != os.getuid() or cwd != str(root / relative):
            raise ServiceError("service_owner_mismatch")
        matches = backend_command_matches(command) if port == 8000 else frontend_command_matches(command, root)
        if not matches:
            raise ServiceError("service_command_mismatch")
    if any(listener_pids(port, environ) != pids for port, pids in observed.items()):
        raise ServiceError("service_listener_changed")


def launch(root: Path, service: str, *, dev: bool, environ: dict[str, str]) -> None:
    config = load_auth(root, environ)
    if service == "backend":
        if importlib.util.find_spec("uvicorn") is None:
            raise ServiceError("uvicorn_dependency_missing")
        cwd, port = root / "backend", 8000
        argv = [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8000"]
        # Preserve existing business overrides, but not Python injection paths or
        # uvicorn environment switches that could enable reload/multiple workers.
        child_env = {
            key: value for key, value in environ.items()
            if not key.startswith("UVICORN_") and key not in {
                "WEB_CONCURRENCY", "PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP", "PYTHONINSPECT",
            }
        }
        child_env.update(ASHARE_AUTH_MODE=config.mode, ASHARE_API_TOKEN=config.token)
        child_env["PYTHONPATH"] = ""
    else:
        cwd, port = root / "apps/web", 3000
        child_env = frontend_environment(config, environ, dev=dev)
        node = shutil.which("node", path=child_env.get("PATH", os.defpath))
        entry = cwd / "node_modules/next/dist/bin/next"
        if not node or not entry.is_file():
            raise ServiceError("next_dependency_missing")
        if not dev:
            build_id = cwd / ".next/BUILD_ID"
            if not build_id.is_file() or not build_id.read_text(encoding="utf-8").strip():
                raise ServiceError("production_build_missing")
        hostname = "0.0.0.0" if config.token else "127.0.0.1"
        argv = [node, str(entry), "dev" if dev else "start", "--hostname", hostname, "--port", "3000"]
    if not cwd.is_dir():
        raise ServiceError("service_directory_missing")
    fd = acquire_port_lock(root, port)
    try:
        if port_is_listening(port, environ):
            raise ServiceError("port_already_listening")
        os.chdir(cwd)
        os.execve(argv[0], argv, child_env)
    finally:
        os.close(fd)  # exec does not return; failed exec releases the launch lease.


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def read_json(url: str, headers: dict[str, str]) -> tuple[int, object, str]:
    """Fixed localhost reads only; do not follow redirects or system proxies."""
    if url not in CHECK_URLS:
        raise ServiceError("check_url_forbidden")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(url, headers={"Accept": "application/json", **headers})
    try:
        with opener.open(request, timeout=5) as response:
            raw = response.read(1_048_577)
            if len(raw) > 1_048_576:
                raise ServiceError("check_response_too_large")
            return response.status, json.loads(raw), response.headers.get("Cache-Control", "")
    except urllib.error.HTTPError as error:
        error.close()
        return error.code, None, error.headers.get("Cache-Control", "")  # No error bodies.
    except (OSError, ValueError):
        raise ServiceError("check_request_failed") from None


def check(config: AuthConfig) -> dict:
    if not config.token:
        raise ServiceError("device_access_requires_server_token")
    status, health, _ = read_json(f"{BACKEND_ORIGIN}/api/health", {})
    if status != 200 or not isinstance(health, dict) or health.get("status") not in {"ok", "degraded"}:
        raise ServiceError("backend_health_unavailable")
    status, data, _ = read_json(f"{BACKEND_ORIGIN}/api/quotes", {})
    if status != (401 if config.token else 200):
        raise ServiceError("backend_auth_posture_mismatch")
    if config.token:
        status, data, _ = read_json(f"{BACKEND_ORIGIN}/api/quotes", {"X-API-Token": config.token})
    if status != 200 or not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ServiceError("backend_authenticated_read_failed")
    status, _, _ = read_json(f"{FRONTEND_ORIGIN}/backend/api/quotes", {})
    if status != 401:
        raise ServiceError("frontend_proxy_pairing_not_enforced")
    status, _, cache_control = read_json(f"{FRONTEND_ORIGIN}/api/ws-credential", {})
    if status != 401 or "no-store" not in cache_control.lower():
        raise ServiceError("frontend_ws_pairing_not_enforced")
    status, state, _ = read_json(f"{FRONTEND_ORIGIN}/api/access/status", {})
    if status != 200 or not isinstance(state, dict) or state.get("state") != "unpaired":
        raise ServiceError("frontend_pairing_bridge_failed")
    return {
        "http_available": True, "proxy_auth": "pairing_required",
        "quote_rows": len(data["data"]), "ws_credential_matches": "retired_long_token_not_exposed",
        "actual_ws": "not_verified_use_existing_browser", "market_freshness": "not_verified",
    }


def pairing_code(config: AuthConfig, *, recover: bool = False) -> dict:
    """Explicit local operator action. The code is shown only in this terminal."""
    if not config.token:
        raise ServiceError("device_access_requires_server_token")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(f"{BACKEND_ORIGIN}/api/access/bootstrap-code",
        data=json.dumps({"recover": recover}).encode(),
        headers={"X-API-Token": config.token, "Content-Type": "application/json"}, method="POST")
    try:
        with opener.open(request, timeout=5) as response:
            data = json.loads(response.read(4096))
            if response.status != 200 or not isinstance(data.get("code"), str):
                raise ServiceError("pairing_code_failed")
            return {"code": data["code"], "expires_in_seconds": 300,
                    "recovery_revokes_all_devices_on_claim": recover}
    except urllib.error.HTTPError as error:
        error.close()
        raise ServiceError("pairing_code_rejected_use_existing_owner_or_explicit_recovery") from None
    except (OSError, ValueError):
        raise ServiceError("pairing_code_failed") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("backend", help="foreground backend on 127.0.0.1:8000; no reload")
    frontend = commands.add_parser("frontend", help="foreground frontend on port 3000; existing build")
    frontend.add_argument("--dev", action="store_true", help="use the installed Next dev server instead")
    commands.add_parser("check", help="fixed read-only HTTP checks; does not open WebSockets")
    pairing = commands.add_parser("pairing-code", help="issue a five-minute local owner initialization code")
    pairing.add_argument("--recover", action="store_true", help="replace owner and revoke all devices when the code is claimed")
    args = parser.parse_args(argv)
    try:
        if args.command in {"check", "pairing-code"}:
            config = load_auth(ROOT, dict(os.environ))
            verify_service_owners(ROOT, dict(os.environ))
            result = check(config) if args.command == "check" else pairing_code(config, recover=args.recover)
            print(json.dumps(result, ensure_ascii=True))
        else:
            launch(ROOT, args.command, dev=getattr(args, "dev", False), environ=dict(os.environ))
        return 0
    except ServiceError as error:
        print(f"local_services: {error}", file=sys.stderr)
    except (OSError, subprocess.SubprocessError):
        print("local_services: operation_failed", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
