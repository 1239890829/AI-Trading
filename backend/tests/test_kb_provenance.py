"""Body provenance, stale/corrupt receipts and real assistant consumption."""
import asyncio
import json

import pytest
from fastapi import HTTPException

from app.picks import kb_routing as kr
from app.assistant.tools import ToolCall, ToolContext, run_tool


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    index = tmp_path / "00-INDEX.md"
    index.write_text("| KB-STOCK-07 | 发现与介入 | ✅ | 2026-09-30 |\n")
    body = tmp_path / "body.md"
    body.write_text("# 册\n### KB-STOCK-07 发现与介入\n来源与内容。\n#### 失效条件\n不能直接买入。\n### 其他章节\n不属于条目。\n")
    monkeypatch.setattr(kr, "KB_INDEX_PATH", index)
    return index, body


def test_index_id_alone_never_certifies_body(corpus):
    ids, raw = kr.snapshot_citations("intraday_pick", ["KB-STOCK-07"])
    refs = json.loads(raw)
    assert json.loads(ids) == [] and refs["state"] == "rejected"
    assert "正文" in refs["conflict"]["KB-STOCK-07"]


def test_exact_fragment_keeps_subheading_and_stops_at_peer(corpus):
    fragment = kr.retrieve_kb("intraday_pick", "KB-STOCK-07")["fragment"]
    assert fragment["start_line"] == 2 and fragment["end_line"] == 5
    assert "不能直接买入" in fragment["text"] and "不属于" not in fragment["text"]
    ids, raw = kr.snapshot_citations("intraday_pick", ["KB-STOCK-07"], fragments=[fragment])
    assert json.loads(ids) == ["KB-STOCK-07"]
    refs = json.loads(raw)
    assert refs["retrieved"] == [fragment]
    assert refs["support"] == [] and refs["relation"] == "unverified"


@pytest.mark.parametrize("field,value", [
    ("text", "改写正文"), ("start_line", 3), ("end_line", 9),
    ("fragment_sha256", "invented"), ("file_sha256", "invented"),
    ("scenario", "pre_open_event"), ("path", "docs/private.md"),
    ("complete", False), ("max_chars", None),
])
def test_modified_receipt_is_rejected(corpus, field, value):
    fragment = kr.retrieve_kb("intraday_pick", "KB-STOCK-07")["fragment"]
    fragment[field] = value
    ids, raw = kr.snapshot_citations("intraday_pick", ["KB-STOCK-07"], fragments=[fragment])
    assert json.loads(ids) == [] and json.loads(raw)["state"] == "rejected"


@pytest.mark.parametrize("change", ["body", "index"])
def test_content_or_index_drift_rejects_old_receipt(corpus, change):
    index, body = corpus
    fragment = kr.retrieve_kb("intraday_pick", "KB-STOCK-07")["fragment"]
    target = body if change == "body" else index
    target.write_text(target.read_text().replace("发现与介入", "修改后的标题"))
    ids, raw = kr.snapshot_citations("intraday_pick", ["KB-STOCK-07"], fragments=[fragment])
    assert json.loads(ids) == [] and "版本" in json.loads(raw)["conflict"]["KB-STOCK-07"]


@pytest.mark.parametrize("failure", ["missing", "duplicate", "utf8", "symlink"])
def test_bad_corpus_fails_visibly(corpus, failure, tmp_path):
    _, body = corpus
    if failure == "missing":
        body.write_text("只有 [[KB-STOCK-07]] 引用，没有定义")
    elif failure == "duplicate":
        (tmp_path / "second.md").write_text("### KB-STOCK-07 重复\n正文")
    elif failure == "utf8":
        body.write_bytes(b"\xff")
    else:
        outside = tmp_path.parent / "outside-kb.md"
        outside.write_text("私人上下文")
        (tmp_path / "escape.md").symlink_to(outside)
    result = kr.retrieve_kb("intraday_pick", "KB-STOCK-07")
    assert result["state"] == "rejected" and result["fragment"] is None


def test_fenced_heading_does_not_create_a_definition(corpus):
    _, body = corpus
    body.write_text(body.read_text() + "```markdown\n### KB-STOCK-07 假定义\n```\n")
    assert kr.retrieve_kb("intraday_pick", "KB-STOCK-07")["state"] == "retrieved"


def test_paging_discloses_missing_tail_and_never_cuts_lines(corpus):
    _, body = corpus
    body.write_text("### KB-STOCK-07 标题\n" + "正文\n" * 50 + "尾部反证。\n")
    first = kr.retrieve_kb("intraday_pick", "KB-STOCK-07", max_chars=100)["fragment"]
    assert not first["complete"] and first["next_line"]
    tail = kr.retrieve_kb("intraday_pick", "KB-STOCK-07", start_line=first["next_line"])["fragment"]
    assert "尾部反证" in tail["text"] and not tail["complete"]
    assert tail["next_line"] is None
    ids, raw = kr.snapshot_citations("intraday_pick", ["KB-STOCK-07"], fragments=[first, tail])
    assert json.loads(ids) == ["KB-STOCK-07"] and len(json.loads(raw)["retrieved"]) == 2


def test_assistant_receives_actual_body_and_receipt(corpus):
    output = asyncio.run(run_tool(ToolCall("kb", {"id": "KB-STOCK-07", "scenario": "intraday_pick"}), ToolContext(provider=None), cache=None))
    receipt = json.loads(output.split("\n知识仅", 1)[0])
    assert "不能直接买入" in receipt["fragment"]["text"]
    assert "语义支持" in output and "不构成买卖建议" in output


def test_kb_tool_does_not_reuse_stale_generic_cache(corpus):
    from app.core.ttl_cache import TTLCache
    cache = TTLCache("test.kb", ttl=30)
    ctx = ToolContext(provider=None)
    call = ToolCall("kb", {"id": "KB-STOCK-07", "scenario": "intraday_pick"})
    first = asyncio.run(run_tool(call, ctx, cache=cache))
    _, body = corpus
    body.write_text(body.read_text().replace("不能直接买入", "修改后的失效条件"))
    second = asyncio.run(run_tool(call, ctx, cache=cache))
    assert first != second and "修改后的失效条件" in second
    assert "命中缓存" not in second


def test_snapshot_reads_corpus_once_for_multiple_pages(corpus, monkeypatch):
    _, body = corpus
    body.write_text("### KB-STOCK-07 标题\n" + "正文\n" * 50 + "尾部反证。\n")
    first = kr.retrieve_kb("intraday_pick", "KB-STOCK-07", max_chars=100)["fragment"]
    tail = kr.retrieve_kb("intraday_pick", "KB-STOCK-07", start_line=first["next_line"])["fragment"]
    original = kr._read_kb_sources
    calls = []
    def counted():
        calls.append(True)
        return original()
    monkeypatch.setattr(kr, "_read_kb_sources", counted)
    ids, _ = kr.snapshot_citations("intraday_pick", ["KB-STOCK-07"], fragments=[first, tail])
    assert json.loads(ids) == ["KB-STOCK-07"] and len(calls) == 1


def test_reader_rejects_sibling_prefix_escape(tmp_path, monkeypatch):
    from app.api.routes import agent
    root = tmp_path / "docs"
    root.mkdir()
    sibling = tmp_path / "docs-private"
    sibling.mkdir()
    (sibling / "secret.md").write_text("private")
    monkeypatch.setattr(agent, "_docs_root", lambda: root)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(agent.kb_file("../docs-private/secret.md"))
    assert exc.value.status_code == 404


def test_all_stock_entries_resolve_with_counterevidence():
    index = kr.load_kb_index()
    stock = [kb_id for kb_id in index.entries if kb_id.startswith("KB-STOCK-")]
    assert len(stock) == 37
    for kb_id in stock:
        result = kr.retrieve_kb("post_close_review", kb_id, max_chars=12000, index=index)
        assert result["state"] == "retrieved", (kb_id, result)
    body = kr.retrieve_kb("post_close_review", "KB-STOCK-32", max_chars=12000)["fragment"]["text"]
    assert "附记" in body and "盘中口径终判" in body
