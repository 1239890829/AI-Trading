"""MIG-0 recovery preserves local assets, including links, without traversing them."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("platform_assets", ROOT / "scripts/audit/platform_assets.py")
assets = importlib.util.module_from_spec(spec)
spec.loader.exec_module(assets)


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    root.mkdir()
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / ".gitignore").write_text(".workbuddy/ignored\n")
    legacy = root / ".workbuddy"
    legacy.mkdir()
    (legacy / "tracked").write_text("project knowledge")
    (legacy / "ignored").write_bytes(b"private recovery\0")
    (legacy / "untracked").write_text("unknown asset")
    (legacy / "empty").mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("must not enter backup")
    (legacy / "external-link").symlink_to(outside, target_is_directory=True)
    subprocess.run(["git", "-C", str(root), "add", ".gitignore", ".workbuddy/tracked"], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "fixture"], check=True)
    return root


def test_backup_covers_git_states_and_preserves_links_without_following(project, tmp_path):
    before = assets.inventory(project)
    output = tmp_path / "recovery"
    assets.backup(project, output)
    manifest = json.loads((output / "manifest.json").read_text())
    assert manifest["records"] == before == assets.inventory(project)
    assert {r["git"] for r in before} == {"tracked", "untracked", "ignored"}
    assert {r["kind"] for r in before} == {"file", "directory", "symlink"}
    with tarfile.open(output / "recovery.tar.gz") as bundle:
        assert len(bundle.getmembers()) == len(before)
        assert not any("secret" in m.name for m in bundle.getmembers())
        member = bundle.getmember("primary/external-link")
        assert member.issym() and member.linkname == str(tmp_path / "outside")
        # Restore a payload into an isolated, new file: not just archive metadata.
        restored = tmp_path / "restored"
        restored.write_bytes(bundle.extractfile("primary/ignored").read())
    assert restored.read_bytes() == (project / ".workbuddy/ignored").read_bytes()
    assert output.stat().st_mode & 0o777 == 0o700
    assert (output / "recovery.tar.gz").stat().st_mode & 0o777 == 0o600


def test_backup_refuses_source_root_links(project, tmp_path):
    (project / ".workbuddy-ai").symlink_to(tmp_path / "outside", target_is_directory=True)
    with pytest.raises(ValueError, match="Source root"):
        assets.backup(project, tmp_path / "recovery")


def test_backup_refuses_destination_inside_source(project):
    with pytest.raises(ValueError, match="outside every source"):
        assets.backup(project, project / ".workbuddy/recovery")
    assert not (project / ".workbuddy/recovery").exists()


def test_backup_never_overwrites_a_recovery_point(project, tmp_path):
    output = tmp_path / "recovery"
    assets.backup(project, output)
    saved = (output / "recovery.tar.gz").read_bytes()
    with pytest.raises(FileExistsError):
        assets.backup(project, output)
    assert (output / "recovery.tar.gz").read_bytes() == saved


def test_source_change_during_backup_is_not_claimed_verified(project, tmp_path, monkeypatch):
    original = assets.inventory
    calls = 0

    def changed(root):
        nonlocal calls
        calls += 1
        if calls == 2:
            (root / ".workbuddy/untracked").write_text("changed while copying")
        return original(root)

    monkeypatch.setattr(assets, "inventory", changed)
    with pytest.raises(ValueError, match="Source changed"):
        assets.backup(project, tmp_path / "recovery")
    assert not (tmp_path / "recovery/manifest.json").exists()
