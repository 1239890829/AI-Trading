"""实体字典换代时，名称识别与行情溯源必须使用同一份当前字典。"""
from __future__ import annotations

import asyncio
import gc
import re
import weakref
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.assistant import context
from app.schemas.market import Quote


@pytest.mark.parametrize("previous_name", [None, "平安银行"], ids=["empty", "different-stock"])
def test_reused_list_id_keeps_current_name_and_quote_sources(monkeypatch, previous_name):
    # 本机已复现空列表地址被新字典复用；固定地址使该边界与分配器无关。
    monkeypatch.setattr(context, "id", lambda _stocks: -1 if previous_name is None else -2, raising=False)
    previous = [] if previous_name is None else [{"name": previous_name, "code": "000001"}]
    context.resolve_symbols("平安银行现在多少？", None, previous)

    current = [{"name": "贵州茅台", "code": "600519"}]
    codes = context.resolve_symbols("贵州茅台现在多少？", None, current)
    batches = []

    async def fetch_quotes(symbols):
        batches.append(symbols)
        return [Quote(symbol="600519", name="贵州茅台", source="tencent", price=1500.5,
                      data_timestamp=datetime(2026, 10, 13, 7, 0, tzinfo=timezone.utc))]

    block, sources = asyncio.run(context.build_market_context(fetch_quotes, codes))

    assert batches == [["600519"]]
    assert "贵州茅台（600519）：现价 1500.5" in block
    assert sources == [{"symbol": "600519", "name": "贵州茅台", "source": "tencent", "as_of": "15:00"}]


def test_current_dictionary_reuses_regex_and_cache_retains_only_latest(monkeypatch):
    compiled = []

    def compile_regex(pattern):
        compiled.append(pattern)
        return re.compile(pattern)

    monkeypatch.setattr(context, "re", SimpleNamespace(compile=compile_regex, escape=re.escape))

    class Stocks(list):
        pass

    first = Stocks([{"name": "贵州茅台", "code": "600519"}])
    first_ref = weakref.ref(first)
    assert context.resolve_symbols("贵州茅台现在多少？", None, first) == ["600519"]
    assert context.resolve_symbols("600519现在多少？", None, first) == ["600519"]
    assert len(compiled) == 1

    current = Stocks([{"name": "平安银行", "code": "000001"}])
    current_ref = weakref.ref(current)
    assert context.resolve_symbols("平安银行现在多少？", None, current) == ["000001"]
    assert context.resolve_symbols("贵州茅台现在多少？", None, current) == []
    assert len(compiled) == 2
    del first, current
    gc.collect()
    assert first_ref() is None  # 已替换的字典不因缓存而长期存活。
    assert current_ref() is not None  # 保留当前真实对象，防止地址复用误命中。

    latest = Stocks([{"name": "慈文传媒", "code": "002343"}])
    assert context.resolve_symbols("慈文传媒现在多少？", None, latest) == ["002343"]
    assert context.resolve_symbols("平安银行现在多少？", None, latest) == []
    assert len(compiled) == 3
    gc.collect()
    assert current_ref() is None  # 继续换代也只保留最后一份。
