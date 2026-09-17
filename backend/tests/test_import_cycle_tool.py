"""Migration must preserve useful analysis without preserving false assurances."""
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts/audit/import_cycles.py"
spec = importlib.util.spec_from_file_location("import_cycles", SCRIPT)
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def make(root, name, text):
    p = root / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text)
    return p


def test_class_body_is_eager_but_function_body_is_deferred(tmp_path):
    make(tmp_path, "app/a.py", "class A:\n import app.b\ndef later():\n import app.c\n")
    make(tmp_path, "app/b.py", "import app.a\n")
    make(tmp_path, "app/c.py", "def later():\n import app.a\n")
    eager, deferred = tool.build_graph(tmp_path)
    assert eager["app.a"] == {"app.b"}
    assert deferred["app.a"] == {"app.c"}
    assert tool.strongly_connected(eager) == [["app.a", "app.b"]]
    assert tool.main([str(tmp_path)]) == 1


def test_relative_submodule_aliases_and_shortest_real_cycle(tmp_path):
    make(tmp_path, "app/__init__.py", "")
    make(tmp_path, "app/a.py", "from . import b as imported\n")
    make(tmp_path, "app/b.py", "from app import a\n")
    eager, _ = tool.build_graph(tmp_path)
    component, = tool.strongly_connected(eager)
    path = tool.shortest_cycle(eager, component)
    assert path[0] == path[-1] and len(path) == 3
    assert all(b in eager[a] for a, b in zip(path, path[1:]))


def test_deferred_cycles_are_not_reported_as_startup_failure(tmp_path):
    make(tmp_path, "app/a.py", "def later():\n import app.b\n")
    make(tmp_path, "app/b.py", "async def later():\n import app.a\n")
    assert tool.main([str(tmp_path)]) == 0


@pytest.mark.parametrize("source", ["def invalid(\n", "from .. import outside\n"])
def test_incomplete_source_never_reports_success(tmp_path, source):
    make(tmp_path, "app/a.py", source)
    assert tool.main([str(tmp_path)]) == 2


def test_venvs_and_symlinks_are_not_followed(tmp_path):
    make(tmp_path, "app/a.py", "import os\n")
    make(tmp_path, ".venv/lib/broken.py", "invalid (\n")
    outside = make(tmp_path, "outside/broken.py", "invalid (\n")
    (tmp_path / "app/link.py").symlink_to(outside)
    (tmp_path / "app/dirlink").symlink_to(outside.parent, target_is_directory=True)
    assert tool.project_files(tmp_path) == [tmp_path / "app/a.py"]
    assert tool.main([str(tmp_path)]) == 0


def test_self_cycle_has_a_reproducible_path():
    graph = {"app.a": {"app.a"}}
    assert tool.strongly_connected(graph) == [["app.a"]]
    assert tool.shortest_cycle(graph, ["app.a"]) == ["app.a", "app.a"]
