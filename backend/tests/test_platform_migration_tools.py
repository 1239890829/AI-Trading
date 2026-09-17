"""Consumer-level smoke checks for the neutral tools and artifact destinations."""
import ast
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def load(relative):
    spec = importlib.util.spec_from_file_location(Path(relative).stem.replace("-", "_"), ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


trash = load("scripts/safe_trash.py")


@pytest.mark.parametrize("kind", ["file", "directory", "symlink", "broken_symlink"])
def test_trash_round_trip_without_following_links(tmp_path, kind):
    root = (tmp_path / "project").resolve()
    root.mkdir()
    source = root / "asset"
    outside = tmp_path / "outside"
    outside.write_text("unchanged")
    if kind == "file": source.write_bytes(b"evidence\0")
    elif kind == "directory":
        source.mkdir()
        (source / "child").write_text("knowledge")
        (source / "empty").mkdir()
    else:
        source.symlink_to(outside if kind == "symlink" else tmp_path / "absent")
    before = trash.fingerprint(source)
    batch = trash.move(root, source, "reviewed fixture")
    assert not source.exists() and not source.is_symlink()
    assert trash.restore(root, batch) == source
    assert trash.fingerprint(source) == before and outside.read_text() == "unchanged"
    assert not (root / ".workbuddy").exists()


@pytest.mark.parametrize("case", ["outside", "sibling_prefix", "root", "git", "backups", "trash", "parent_link"])
def test_trash_rejects_out_of_scope_moves(tmp_path, case):
    root = (tmp_path / "project").resolve()
    root.mkdir()
    (root / "link").symlink_to(tmp_path)
    paths = {"outside": tmp_path / "x", "sibling_prefix": tmp_path / "project-other/x",
             "root": root, "git": root / ".git/config", "backups": root / "artifacts/backups/x",
             "trash": root / "artifacts/trash/x", "parent_link": root / "link/x"}
    with pytest.raises(ValueError):
        trash.move(root, paths[case], "fixture")
    assert not (root / "artifacts").exists()


def test_restore_refuses_overwrite_and_detects_corruption(tmp_path):
    root = tmp_path.resolve()
    source = root / "file"
    source.write_text("old")
    batch = trash.move(root, source, "fixture")
    source.write_text("new")
    with pytest.raises(FileExistsError):
        trash.restore(root, batch)
    assert source.read_text() == "new"
    source.unlink()
    (root / "artifacts/trash" / batch / "payload").write_text("changed")
    with pytest.raises(ValueError, match="checksum"):
        trash.restore(root, batch)


def test_restore_rejects_journal_escape_and_destination_links(tmp_path):
    root = (tmp_path / "project").resolve()
    root.mkdir()
    source = root / "file"
    source.write_text("content")
    batch = trash.move(root, source, "fixture")
    journal = root / "artifacts/trash" / batch / "record.json"
    record = json.loads(journal.read_text())
    record["original"] = "../escaped"
    journal.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        trash.restore(root, batch)
    (root / "link").symlink_to(tmp_path)
    record["original"] = "link/escaped"
    journal.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        trash.restore(root, batch)
    assert not (tmp_path / "escaped").exists()


def test_failed_journal_update_keeps_original_recovery_record(tmp_path, monkeypatch):
    root = tmp_path.resolve()
    source = root / "file"
    source.write_text("recover me")
    replace = Path.replace
    calls = 0

    def fail_second_replace(path, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk error")
        return replace(path, destination)

    with monkeypatch.context() as patch:
        patch.setattr(Path, "replace", fail_second_replace)
        with pytest.raises(OSError, match="disk error"):
            trash.move(root, source, "fixture")
    journal = next((root / "artifacts/trash").glob("*/record.json"))
    assert json.loads(journal.read_text())["state"] == "prepared"
    assert trash.restore(root, journal.parent.name).read_text() == "recover me"


def test_report_cli_keeps_content_css_and_progresses_on_plain_pipe_lines(tmp_path):
    src, dst = tmp_path / "report.md", tmp_path / "report.html"
    src.write_text("# Report\n\n| plain text\n\n---not-a-rule\n\n| A | B |\n|---|---|\n| x | `bool \\| null` |\n")
    subprocess.run([sys.executable, str(ROOT / "scripts/reports/md-report-html.py"), str(src), str(dst),
                    "--title", "Smoke report"], check=True, capture_output=True, timeout=5)
    result = subprocess.run([sys.executable, str(ROOT / "scripts/reports/md-html-parity.py"), str(src), str(dst)],
                            capture_output=True, text=True, timeout=5)
    assert result.returncode == 0, result.stdout
    html = dst.read_text()
    assert "<title>Smoke report</title>" in html
    assert (ROOT / "scripts/reports/report_style.css").read_text() in html
    dst.write_text(html.replace("plain text", "lost"))
    result = subprocess.run([sys.executable, str(ROOT / "scripts/reports/md-html-parity.py"), str(src), str(dst)],
                            capture_output=True, timeout=5)
    assert result.returncode == 1


def test_report_links_cannot_inject_attributes_or_executable_urls():
    report = load("scripts/reports/md-report-html.py")
    html = report.render('[x](https://example.com/" onclick="bad) [x](javascript:bad) <script>bad</script>')
    assert '" onclick="bad' not in html and 'href="javascript:' not in html
    assert "<script>" not in html and "&lt;script&gt;" in html


def test_openapi_comparison_detects_schema_changes_even_if_counts_match():
    tool = load("scripts/audit/openapi-contract-diff.py")
    base = {"paths": {"/quote": {}}, "components": {"schemas": {"Quote": {"type": "number"}}}}
    changed = {"paths": {"/quote": {}}, "components": {"schemas": {"Quote": {"type": "string"}}}}
    assert tool.differences(base, changed) == ["$/components/schemas/Quote/type: value changed"]
    assert tool.differences(base, base) == []


@pytest.mark.parametrize("name", ["replay_sweep_phase.py", "verify_triple_volume_strata.py",
                                  "verify_ml_predict.py", "vbt_benchmark_sample.py"])
def test_research_output_destination_is_inside_neutral_artifacts(tmp_path, name):
    source = (ROOT / "backend/scripts" / name).read_text()
    tree = ast.parse(source)
    candidates = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                  and any(isinstance(t, ast.Name) and t.id in {"out", "REPORT_DIR"} for t in node.targets)
                  and "__file__" in ast.unparse(node.value)]
    assert len(candidates) == 1
    # Evaluate only the actual path expression; never run experiments or touch real data.
    target = eval(compile(ast.Expression(candidates[0].value), name, "eval"),
                  {"Path": Path, "__file__": str(tmp_path / "backend/scripts" / name)})
    assert target.is_relative_to(tmp_path / "artifacts/research")
    assert '".workbuddy"' not in source


def test_patch_archiving_stays_review_only_and_protects_new_artifacts(tmp_path, monkeypatch):
    from app.services import code_executor as ce
    assert ce.PATCH_ARCHIVE_DIR == ce.PROJECT_ROOT / "artifacts/evolution-patches"
    monkeypatch.setattr(ce, "PATCH_ARCHIVE_DIR", tmp_path / "artifacts/evolution-patches")
    path = ce._archive_patch("review fixture", "migration-smoke")
    assert path.read_text() == "review fixture\n"
    assert ce._is_protected("backend/app/artifacts/proposal.py")
    assert not (tmp_path / ".workbuddy").exists()
