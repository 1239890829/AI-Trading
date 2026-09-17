"""解析复用不得让已热缓存的源码守卫失去判别力。"""
import ast
import os

import pytest

from tests.source_ast import read_source_ast
from tests import test_import_lint as imports
from tests import test_tradedate_freshness_guards as freshness


def test_same_size_and_mtime_source_change_is_seen(tmp_path):
    path = tmp_path / "source.py"
    path.write_text("value = 1\n", encoding="utf-8")
    stamp = path.stat()
    before = read_source_ast(path)
    path.write_text("value = 2\n", encoding="utf-8")
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert path.stat().st_size == stamp.st_size
    assert path.stat().st_mtime_ns == stamp.st_mtime_ns
    after = read_source_ast(path)
    assert before.body[0].value.value == 1
    assert after.body[0].value.value == 2


def test_invalid_source_after_warm_cache_raises_with_current_filename(tmp_path):
    for name in ("first.py", "second.py"):
        path = tmp_path / name
        path.write_text("value = 1\n", encoding="utf-8")
        read_source_ast(path)
        path.write_text("value = )\n", encoding="utf-8")
        with pytest.raises(SyntaxError) as error:
            read_source_ast(path)
        assert error.value.filename == str(path)
        path.write_text("value = 2\n", encoding="utf-8")
        assert read_source_ast(path).body[0].value.value == 2


def test_import_guard_rejects_injected_dependency_after_warm_cache(tmp_path):
    path = tmp_path / "service.py"
    clean = "import app.core\n"
    path.write_text(clean, encoding="utf-8")
    before = ast.dump(read_source_ast(path), include_attributes=True)
    imports.test_business_layer_never_imports_api(path, "services/service.py")
    assert ast.dump(read_source_ast(path), include_attributes=True) == before
    path.write_text("import app.api\n", encoding="utf-8")
    with pytest.raises(AssertionError, match="反向依赖 API"):
        imports.test_business_layer_never_imports_api(path, "services/service.py")
    path.write_text(clean, encoding="utf-8")
    imports.test_business_layer_never_imports_api(path, "services/service.py")


def test_freshness_guard_sees_changed_and_new_files(tmp_path, monkeypatch):
    app = tmp_path / "app"
    app.mkdir()
    path = app / "service.py"
    clean = 'cache_on(hub, "neutral", 60)\n'
    bad = 'cache_on(hub, "provider.trading_days", 86400)\n'
    path.write_text(clean, encoding="utf-8")
    monkeypatch.setattr(freshness, "BACKEND", tmp_path)
    before = ast.dump(read_source_ast(path), include_attributes=True)
    freshness.test_b2_no_cache_named_after_trading_days()
    assert ast.dump(read_source_ast(path), include_attributes=True) == before
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(AssertionError, match="trading_days"):
        freshness.test_b2_no_cache_named_after_trading_days()
    path.write_text(clean, encoding="utf-8")
    freshness.test_b2_no_cache_named_after_trading_days()
    (app / "new_service.py").write_text(bad, encoding="utf-8")
    with pytest.raises(AssertionError, match="new_service.py"):
        freshness.test_b2_no_cache_named_after_trading_days()
