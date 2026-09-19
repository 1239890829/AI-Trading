"""Public repository secret/privacy guard."""
from __future__ import annotations

import importlib.util
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "audit" / "public_repo_scan.py"

_spec = importlib.util.spec_from_file_location("public_repo_scan", SCRIPT)
assert _spec and _spec.loader
scan = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scan)


def test_local_email_pattern_does_not_match_localhost_url():
    pat = re.compile(scan.GREP_PATTERNS["local_email"])
    assert pat.search("driver://user:pass@localhost/dbname") is None
    synthetic_local_email = "person@" + "machine" + ".local"
    assert pat.search(synthetic_local_email) is not None


def test_forbidden_secret_filenames_are_fail_closed():
    assert scan.forbidden_filename(".env.production") is True
    assert scan.forbidden_filename("backend/private.pem") is True
    assert scan.forbidden_filename("data/private.sqlite") is True
    assert scan.forbidden_filename(".env.example") is False
    assert scan.forbidden_filename("apps/web/.env.development") is False


def test_placeholder_secret_values_include_replace_me():
    assert scan.PLACEHOLDER_VALUE.match("replace-me")
    assert scan.PLACEHOLDER_VALUE.match("YOUR_API_KEY")
    assert scan.PLACEHOLDER_VALUE.match("")
    assert scan.PLACEHOLDER_VALUE.match("real-secret-value") is None


def test_secret_patterns_detect_dynamic_provider_tokens():
    github_like = "ghp_" + ("A" * 24)
    openrouter_like = "sk-or-v1-" + ("a" * 64)
    typesafe_like = "apikey_" + ("b" * 40)
    assert re.search(scan.GREP_PATTERNS["github_token"], github_like)
    assert re.search(scan.GREP_PATTERNS["openrouter_key"], openrouter_like)
    assert re.search(scan.GREP_PATTERNS["typesafe_key"], typesafe_like)


def test_tracked_development_env_is_explicitly_local_only():
    assert scan.env_problems("apps/web/.env.development") == []


def test_current_tracked_repository_passes_public_repo_scan():
    assert scan.scan() == []
