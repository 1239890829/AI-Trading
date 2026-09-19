#!/usr/bin/env python3
"""Fast public-repository secret/privacy scan for tracked files.

Reports detector + path + line only; never prints matched secret values.
Designed for required CI on a public repository.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GREP_PATTERNS = {
    "typesafe_key": r"apikey_[A-Za-z0-9_-]{20,}",
    "openrouter_key": r"sk-or-v1-[A-Za-z0-9_-]{20,}",
    "anthropic_key": r"sk-ant-[A-Za-z0-9_-]{20,}",
    "openai_like_key": r"(^|[^A-Za-z0-9])sk-[A-Za-z0-9_-]{20,}",
    "github_token": r"gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}",
    "aws_access_key": r"AKIA[0-9A-Z]{16}",
    "google_api_key": r"AIza[0-9A-Za-z_-]{30,}",
    "feishu_webhook": r"https?://(open\.feishu\.cn|open\.larksuite\.com)/open-apis/bot/v2/hook/[0-9A-Za-z-]{16,}",
    "slack_webhook": r"https?://hooks\.slack\.com/services/[A-Za-z0-9/_-]{20,}",
    "discord_webhook": r"https?://discord(app)?\.com/api/webhooks/[0-9]+/[A-Za-z0-9._-]{20,}",
    "telegram_bot_token": r"[0-9]{8,12}:[A-Za-z0-9_-]{30,}",
    "stripe_live_key": r"sk_live_[A-Za-z0-9]{16,}",
    "twilio_api_key": r"SK[0-9a-fA-F]{32}",
    "sendgrid_key": r"SG\.[A-Za-z0-9_-]{16,}\.[A-Za-z0-9_-]{16,}",
    "huggingface_token": r"hf_[A-Za-z0-9]{20,}",
    "npm_token": r"npm_[A-Za-z0-9]{20,}",
    "gitlab_pat": r"glpat-[A-Za-z0-9_-]{20,}",
    "private_key_block": r"-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----",
    "basic_auth_url": r"https?://[^[:space:]/:]+:[^[:space:]/@]+@",
    "local_email": r"[A-Za-z0-9._%+-]+@([A-Za-z0-9._-]+\.)?local\b",
    "local_hostname": r"[A-Za-z0-9._-]*MacBook[A-Za-z0-9._-]*\.local",
    "unix_home_path": r"/(Users|home)/[^/[:space:]\"']+/",
    "windows_home_path": r"[A-Za-z]:\\Users\\[^\\[:space:]\"']+\\",
}

SENSITIVE_ENV_KEY = re.compile(
    r"(?i)(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PASSWD|AUTH_TOKEN|PRIVATE_KEY)"
)
PLACEHOLDER_VALUE = re.compile(
    r"(?i)^(?:|changeme|change_me|replace[-_]?me|your[_-].*|example|placeholder|"
    r"dummy(?:[0-9_-]*)?|test(?:ing)?|xxx+|<.*>|\$\{.*\}|none|null|false|true)$"
)

GENERIC_HOME_NAMES = {
    "test",
    "tester",
    "user",
    "runner",
    "x",
    "ubuntu",
    "root",
    "example",
    "<user>",
}

SAFE_ENV_FILES = {
    ".env.example",
    "apps/web/.env.local.example",
    "skills/hithink-finance/.env.example",
}
SAFE_TRACKED_DEVELOPMENT_ENV = "apps/web/.env.development"
SAFE_DEVELOPMENT_KEYS = {"NEXT_PUBLIC_WS_BASE"}

FORBIDDEN_NAMES = {
    "id_rsa",
    "id_ed25519",
    "credentials.json",
    "secrets.json",
    "secrets.yaml",
    "secrets.yml",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
}
FORBIDDEN_EXTENSIONS = {".pem", ".key", ".p12", ".pfx", ".jks", ".keystore", ".db", ".sqlite", ".sqlite3"}


def tracked_files() -> list[str]:
    raw = subprocess.check_output(["git", "ls-files", "-z"], cwd=ROOT)
    return [item.decode() for item in raw.split(b"\0") if item]


def git_grep(pattern: str) -> list[tuple[str, int]]:
    proc = subprocess.run(
        ["git", "grep", "-I", "-n", "-E", "-e", pattern, "--"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    if proc.returncode not in {0, 1}:
        raise RuntimeError(proc.stderr.strip() or "git grep failed")
    out: list[tuple[str, int]] = []
    for raw in proc.stdout.splitlines():
        parts = raw.split(":", 2)
        if len(parts) < 3:
            continue
        path, line = parts[0], parts[1]
        try:
            out.append((path, int(line)))
        except ValueError:
            continue
    return out


def forbidden_filename(path: str) -> bool:
    p = Path(path)
    name = p.name.lower()
    if path in SAFE_ENV_FILES or path == SAFE_TRACKED_DEVELOPMENT_ENV:
        return False
    if name == ".env" or name.startswith(".env."):
        return True
    if name in FORBIDDEN_NAMES:
        return True
    return p.suffix.lower() in FORBIDDEN_EXTENSIONS


def env_problems(path: str) -> list[tuple[int, str]]:
    p = ROOT / path
    if not p.exists():
        return []
    problems: list[tuple[int, str]] = []
    for lineno, raw in enumerate(p.read_text(errors="ignore").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if path == SAFE_TRACKED_DEVELOPMENT_ENV:
            if key not in SAFE_DEVELOPMENT_KEYS:
                problems.append((lineno, f"unexpected_development_env_key:{key}"))
            elif not value.startswith(("ws://127.0.0.1", "ws://localhost")):
                problems.append((lineno, f"nonlocal_development_env_value:{key}"))
        if SENSITIVE_ENV_KEY.search(key) and value and not PLACEHOLDER_VALUE.match(value):
            problems.append((lineno, f"nonplaceholder_secret_env_value:{key}"))
    return problems


def home_name_at(path: str, lineno: int) -> str | None:
    try:
        line = (ROOT / path).read_text(errors="ignore").splitlines()[lineno - 1]
    except Exception:
        return None
    m = re.search(r"/(?:Users|home)/([^/\s\"']+)/", line)
    if m:
        return m.group(1)
    m = re.search(r"[A-Za-z]:\\Users\\([^\\\s\"']+)\\", line)
    return m.group(1) if m else None


def scan() -> list[tuple[str, int, str]]:
    problems: set[tuple[str, int, str]] = set()

    for path in tracked_files():
        if forbidden_filename(path):
            problems.add((path, 0, "forbidden_tracked_secret_filename"))

    for detector, pattern in GREP_PATTERNS.items():
        for path, line in git_grep(pattern):
            if detector in {"unix_home_path", "windows_home_path"}:
                name = home_name_at(path, line)
                if name in GENERIC_HOME_NAMES:
                    continue
            problems.add((path, line, detector))

    for path in SAFE_ENV_FILES | {SAFE_TRACKED_DEVELOPMENT_ENV}:
        for line, detector in env_problems(path):
            problems.add((path, line, detector))

    return sorted(problems)


def main() -> int:
    problems = scan()
    if problems:
        print(f"public-repo scan: {len(problems)} problem(s)")
        for path, line, detector in problems:
            where = f"{path}:{line}" if line else path
            print(f"- {where}: {detector}")
        return 1
    print("public-repo scan: PASS (no detected secrets/personal machine identifiers in tracked files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
