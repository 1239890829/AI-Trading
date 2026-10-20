"""Bounded public issuer disclosures, independent of model and application state.

The website endpoints are observed public interfaces, not a licensed data API or
an availability promise. Extracts are sentence candidates, not verified orders.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import threading
import time

import httpx


# Metadata uses the shared clock. The isolated PDF-only entry point neither
# reads dates nor imports the application/configuration namespace.
if __name__ != "__main__":
    from app.core.bjtime import BJ_TZ as BJ
SITE = "https://www.cninfo.com.cn/new/"
STATIC = "https://static.cninfo.com.cn/"
MAX_PDF = 24 * 1024 * 1024
MAX_JSON = 1024 * 1024
MIN_REQUEST_INTERVAL = 1.0
EXTRACTOR = "bounded-sentences-v3"
_request_lock = threading.Lock()
_last_request = 0.0
_PDF_PATH = re.compile(r"finalpage/(\d{4}-\d{2}-\d{2})/(\d+)\.PDF", re.I)
_REPORT = re.compile(r"20\d{2}年(?:年度|半年度|第[一二三四]季度)报告(?:全文)?(?:[（(][^）)]{0,24}[）)])?$")
_NOTICE = re.compile(r"(?:签订|签署|中标|重大合同|订单|项目投资|投资建设)")
_PATTERNS = {
    "business": r"(?:主要业务|主营业务|主要从事|专注于|专注从事|公司致力于)",
    "product": r"(?:主要产品|产品包括|产品主要|产品为|业务涉及)",
    "customer": r"(?:主要客户|客户包括|客户涵盖|客户为|客户有)",
    "order": r"(?:签订.{0,40}(?:合同|协议)|签署.{0,40}(?:合同|协议)|中标|在手订单|订单交付|定点)",
    "risk": r"(?:风险|不确定|未产生|尚未|不代表|不构成|不保证|交付周期延长)",
}


class SourceError(ValueError):
    """Source failure; never interpreted as an issuer having no business."""


def _failure(error: Exception) -> str:
    # OS/parser/network exception strings may contain local paths or headers.
    # Only our bounded, fixed SourceError messages are fit for public consumers.
    return str(error)[:350] if isinstance(error, SourceError) else type(error).__name__


def _compact(value: str) -> str:
    return re.sub(r"\s+", "", value)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sha(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _read(path: Path, limit=MAX_JSON):
    if path.is_symlink() or path.stat().st_size > limit:
        raise SourceError("unsafe cached metadata")
    return json.loads(path.read_text(encoding="utf-8"))


def _atomic(path: Path, value) -> None:
    if path.is_symlink():
        raise SourceError("refusing symlink cache destination")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".company-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(value, out, ensure_ascii=False, sort_keys=True)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _request(client, method, url, *, cap, **kwargs) -> bytes:
    global _last_request
    with _request_lock:
        delay = MIN_REQUEST_INTERVAL - (time.monotonic() - _last_request)
        if delay > 0:
            time.sleep(delay)
        _last_request = time.monotonic()
        with client.stream(method, url, timeout=12, follow_redirects=False,
                           headers={"User-Agent": "AShare-public-research/1.0",
                                    "Referer": "https://www.cninfo.com.cn/"}, **kwargs) as response:
            if response.status_code != 200:
                raise SourceError(f"official source HTTP {response.status_code}; no redirect/retry")
            raw = bytearray()
            for chunk in response.iter_bytes():
                raw.extend(chunk)
                if len(raw) > cap:
                    raise SourceError("official source response exceeds byte bound")
            return bytes(raw)


def _query(client, root: Path, symbol: str, name: str, endpoint: str, **kwargs):
    raw = _request(client, "POST", SITE + endpoint, cap=MAX_JSON, **kwargs)
    # Content-addressed response bytes are unique. Do not preserve cookies/headers.
    path = root / "metadata" / f"{symbol}-{name}-{_sha(raw)}.json"
    if path.parent.is_symlink() or path.is_symlink():
        raise SourceError("refusing symlink source metadata")
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        with path.open("xb") as out:
            out.write(raw)
    value = json.loads(raw)
    return value, {"endpoint": SITE + endpoint, "raw_sha256": _sha(raw),
                   "received_at": _now().isoformat(), "file": path.name}


def _rows(payload, symbol: str, org: str) -> list[dict]:
    if not isinstance(payload, dict):
        raise SourceError("invalid announcement envelope")
    total, more, rows = (payload.get(k) for k in ("totalAnnouncement", "hasMore", "announcements"))
    if type(total) is not int or total < 0 or type(more) is not bool:
        raise SourceError("invalid announcement count/pagination")
    if rows is None and total == 0 and more is False:
        return []
    if not isinstance(rows, list) or (not rows and (total != 0 or more)):
        raise SourceError("invalid announcement rows; not a valid empty window")
    if not more and total != len(rows):
        raise SourceError("announcement count changed or response truncated")
    seen = set()
    for row in rows:
        if (not isinstance(row, dict) or row.get("secCode") != symbol
                or row.get("orgId") != org or not str(row.get("announcementId", "")).isdigit()):
            raise SourceError("foreign or malformed announcement identity")
        if row["announcementId"] in seen:
            raise SourceError("duplicate announcement identity")
        seen.add(row["announcementId"])
    return rows


def _publication(row: dict) -> tuple[datetime, str, str]:
    value = row.get("announcementTime")
    match = _PDF_PATH.fullmatch(row.get("adjunctUrl", ""))
    if type(value) is not int or not match or row.get("adjunctType", "").upper() != "PDF":
        raise SourceError("missing disclosure clock or official PDF path")
    claimed = datetime.fromtimestamp(value / 1000, BJ)
    if claimed.date().isoformat() != match[1] or match[2] != row["announcementId"]:
        raise SourceError("disclosure date/attachment identity conflict")
    # A midnight website stamp does not prove that the original was available at
    # midnight. Its conservative day upper bound is separate from local receipt.
    day = claimed.hour == claimed.minute == claimed.second == claimed.microsecond == 0
    upper = claimed.replace(hour=23, minute=59, second=59) if day else claimed
    return upper, "day" if day else "source_timestamp", claimed.isoformat()


def _period(title: str) -> str:
    match = re.search(r"(20\d{2})年(年度|半年度|第([一二三四])季度)", title)
    if not match:
        return "临时公告；仅证明披露所述时点与阶段"
    ending = {"年度": "12-31", "半年度": "06-30"}.get(match[2])
    if ending is None:
        ending = {"一": "03-31", "二": "06-30", "三": "09-30", "四": "12-31"}[match[3]]
    return f"{match[1]}-01-01至{match[1]}-{ending}"


def _original(client, root: Path, row: dict) -> tuple[Path, dict]:
    url = STATIC + row["adjunctUrl"]
    path = root / f"{row['announcementId']}.pdf"
    stamp = path.with_suffix(".received.json")
    if stamp.is_symlink() or (not path.exists() and stamp.exists()):
        raise SourceError("original receipt conflict; manual recovery required")
    if path.exists():
        if path.is_symlink() or path.stat().st_size > MAX_PDF:
            raise SourceError("unsafe cached original")
        metadata = _read(stamp)
        raw = path.read_bytes()
        if metadata.get("url") != url or metadata.get("sha256") != _sha(raw):
            raise SourceError("cached original identity/hash drift")
        return path, metadata
    raw = _request(client, "GET", url, cap=MAX_PDF)
    if not raw.startswith(b"%PDF-"):
        raise SourceError("official attachment is not PDF")
    metadata = {"url": url, "sha256": _sha(raw), "received_at": _now().isoformat()}
    with path.open("xb") as out:
        out.write(raw)
    _atomic(stamp, metadata)
    return path, metadata


def _parse_pdf(path: Path) -> dict:
    fd, output = tempfile.mkstemp(prefix=".pdf-text-", suffix=".json", dir=path.parent)
    os.close(fd)
    err_fd, errors = tempfile.mkstemp(prefix=".pdf-error-", dir=path.parent)
    process = None
    try:
        with os.fdopen(err_fd, "wb") as error_file:
            process = subprocess.Popen(
                [sys.executable, "-I", str(Path(__file__).resolve()), "--parse", str(path), output],
                env={"PATH": os.defpath, "LANG": "C.UTF-8", "PYTHONUTF8": "1"},
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=error_file,
            )
            deadline = time.monotonic() + 25
            while process.poll() is None:
                try:
                    process.wait(timeout=0.25)
                except subprocess.TimeoutExpired:
                    if time.monotonic() >= deadline:
                        raise SourceError("bounded PDF parser timed out after 25 seconds")
                    # Darwin rejects AS/DATA limits. Inspect only our child PID;
                    # this is a sampled RSS bound, not an OS memory sandbox.
                    rss = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(process.pid)],
                                         capture_output=True, text=True, timeout=2, check=False)
                    if rss.stdout.strip().isdigit():
                        if int(rss.stdout.strip()) > 768 * 1024:
                            raise SourceError("bounded PDF parser exceeded 768 MiB sampled RSS")
                    elif process.poll() is None:
                        raise SourceError("PDF parser RSS measurement unavailable")
        if process.returncode:
            # Public input parser failures stay diagnostic, never become facts.
            raise SourceError(f"bounded PDF parser failed ({process.returncode}); unsupported, encrypted or oversized PDF")
        return _read(Path(output), 2 * MAX_JSON)
    except subprocess.TimeoutExpired as error:
        raise SourceError("PDF parser resource measurement timed out") from error
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        Path(output).unlink(missing_ok=True)
        Path(errors).unlink(missing_ok=True)


def _identity(pages: list[str], symbol: str) -> tuple[str, int, str]:
    for text in pages[:12]:
        # Full legal issuer names usually occur in the page header/cover. Do not
        # derive one from a news title, theme, fuzzy short name or customer table.
        for line in text.splitlines()[:6]:
            name = re.search(r"[\u4e00-\u9fffA-Za-z（）()·]{2,60}股份有限公司", _compact(line))
            if not name:
                continue
            company = name[0]
            company = re.sub(r"^.*(?:中文名称|公司名称)[:：]?", "", company)
            for index, page in enumerate(pages[:12], 1):
                compact = _compact(page)
                for code in re.finditer(rf"(?<!\d){symbol}(?!\d)", compact):
                    occurrences = list(re.finditer(re.escape(company), compact))
                    for occurrence in occurrences:
                        start, end = min(code.start(), occurrence.start()), max(code.end(), occurrence.end())
                        if end - start <= 280:
                            return company, index, compact[start:end]
    raise SourceError("full issuer name and exact security code not verified on same page")


def _excerpts(pages: list[str]) -> tuple[list[dict], list[str]]:
    choices = {kind: [] for kind in _PATTERNS}
    omitted = False
    for page, text in enumerate(pages, 1):
        compact = _compact(text)
        sentences = list(re.finditer(r"[^。！？；]+[。！？；]", compact))
        for index, sentence in enumerate(sentences):
            body = sentence[0]
            numbers = re.findall(r"\d+(?:[,.]\d+)*", body)
            # Table runs can contain a matching heading such as risk/order but
            # do not provide a complete narrative assertion. Keep the original;
            # reject only this candidate and report bounded coverage below.
            digits = sum(ch.isdigit() for ch in body)
            if (len(numbers) >= 8 or (len(numbers) >= 4 and sum(map(len, numbers)) > len(body) / 5)
                    or (digits >= 24 and digits > len(body) / 3)):
                omitted = True
                continue
            for kind, pattern in _PATTERNS.items():
                if not re.search(pattern, body):
                    continue
                quote = body
                # Tables and headers can precede the first narrative full stop.
                # Keep a contiguous source suffix starting at its actual subject.
                subject = re.search(r"(?:公司|本公司)(?:主营业务|主要(?:从事|产品|客户|业务)|致力于|专注|从事|产品|业务)", body)
                if subject and subject.start() > 24 and re.search(pattern, body[subject.start():]):
                    quote = body[subject.start():]
                if (index and re.match(r"^(?:该|上述|其中|本项目|因此)", quote)
                        and len(sentences[index - 1][0]) + len(quote) <= 1000):
                    quote = sentences[index - 1][0] + quote
                if re.search(r"(?:参见.{0,30}相关内容|公司需遵守|本公司及董事会全体成员保证)", quote):
                    continue  # Navigation/legal boilerplate is not business evidence.
                if kind == "risk" and re.search(r"(?:前瞻性陈述|请查阅|敬请|详见|重大风险提示|请投资者认真阅读|特别提示投资者关注)", quote):
                    continue  # Navigation/disclaimers are not operating risks.
                if kind == "order" and re.search(r"(?:一致行动协议|有限合伙协议|捐赠资金|买卖合同纠纷)", quote):
                    continue  # Governance/investment/disputes are not customer orders.
                if kind == "customer" and re.search(r"(?:以客户为导向|客户导向)", quote):
                    continue  # Customer-oriented marketing does not identify customers.
                if not 24 <= len(quote) <= 1000:
                    omitted = True
                    continue
                if quote not in [v["quote"] for v in choices[kind]]:
                    choices[kind].append({"kind": kind, "page": page, "quote": quote})
    for kind, values in choices.items():
        if kind == "risk":
            values.sort(key=lambda v: not bool(re.search(r"汇率|回款|延长|下降|下滑|减值|亏损|违约|集中", v["quote"])))
        elif kind == "order":
            values.sort(key=lambda v: bool(re.search(r"生产模式|采购模式|销售流程|采购框架|质量保证协议", v["quote"])))
    result, seen = [], set()
    candidates = {k: list(v[:3]) for k, v in choices.items()}
    for _ in range(12):
        progress = False
        for kind in _PATTERNS:
            while candidates[kind] and candidates[kind][0]["quote"] in seen:
                candidates[kind].pop(0)
            if candidates[kind] and len(result) < 12:
                item = candidates[kind].pop(0)
                result.append(item)
                seen.add(item["quote"])
                progress = True
        if not progress or len(result) == 12:
            break
    missing = [f"本次有界抽取未取得{kind}类完整句，不代表公司未披露或不存在。"
               for kind, values in choices.items() if not values]
    if omitted or any(len(v) > 3 for v in choices.values()) or sum(map(len, choices.values())) > len(result):
        missing.append("候选句超过长度/数量上限，已截断；未覆盖全部报告、表格或公司事实。")
    return result, missing


def collect_company(symbol: str, *, as_of: datetime, root: Path, client=None) -> dict:
    """Get up to two official documents, with explicit partial coverage/unknowns.

    ``as_of`` limits source disclosure time, not retrospectively verified access.
    The caller must capture its final packet after actual document receipt.
    """
    if not re.fullmatch(r"\d{6}", symbol) or as_of.utcoffset() is None:
        raise ValueError("six-digit symbol and timezone-aware as_of required")
    root = Path(root)
    if root.is_symlink() or (root / "metadata").is_symlink():
        raise SourceError("refusing symlink source cache directory")
    root.mkdir(parents=True, exist_ok=True)
    cache = root / f"{symbol}.manifest.json"
    checked = _now()
    cache_error = None
    if cache.exists() or cache.is_symlink():
        try:
            old = _read(cache)
            if (old.get("symbol") == symbol and old.get("extractor") == EXTRACTOR
                    and datetime.fromisoformat(old["cache_until"]) > checked
                    and datetime.fromisoformat(old["as_of"]) <= as_of):
                for doc in old["documents"]:
                    if not re.fullmatch(r"\d+", doc["announcement_id"]):
                        raise SourceError("unsafe cached announcement identity")
                    raw = root / f"{doc['announcement_id']}.pdf"
                    if raw.is_symlink() or raw.stat().st_size > MAX_PDF or _sha(raw.read_bytes()) != doc["sha256"]:
                        raise SourceError("cached original drift; cached result not reused")
                return {**old, "cache_hit": True}
        except (OSError, ValueError, TypeError, KeyError) as error:
            cache_error = _failure(error)
    result = {"symbol": symbol, "company_name": None, "short_name": None, "documents": [],
              "missing": [], "status": "missing", "checked_at": checked.isoformat(),
              "as_of": as_of.isoformat(), "extractor": EXTRACTOR, "cache_hit": False,
              "provenance": {"source": "CNINFO public website", "queries": [],
                             "point_in_time_first_seen_verified": False,
                             "automated_excerpts_are_candidates": True,
                             "official_automation_quota": None,
                             "bulk_redistribution_permission": "not established"}}
    owned = client is None
    client = client or httpx.Client(trust_env=False)
    try:
        if cache_error:
            raise SourceError("cached source metadata/original drift: " + cache_error)
        identities, proof = _query(client, root, symbol, "identity", "information/topSearch/query",
                                   params={"keyWord": symbol, "maxNum": 10})
        result["provenance"]["queries"].append(proof)
        exact = [x for x in identities if isinstance(x, dict) and x.get("code") == symbol
                 and x.get("category") == "A股"] if isinstance(identities, list) else []
        if len(exact) != 1 or not re.fullmatch(r"[A-Za-z0-9]{1,30}", exact[0].get("orgId", "")):
            raise SourceError("exact unique A-share issuer identity unavailable")
        issuer = exact[0]
        result["short_name"] = issuer.get("zwjc")
        selected = []
        for query_name, keyword, days in (("reports", "报告", 550), ("recent", "", 45)):
            end = as_of.astimezone(BJ).date()
            form = {"pageNum": 1, "pageSize": 30, "column": "szse", "tabName": "fulltext",
                    "stock": symbol + "," + issuer["orgId"], "searchkey": keyword,
                    "seDate": f"{end - timedelta(days=days)}~{end}", "isHLtitle": "false"}
            payload, proof = _query(client, root, symbol, query_name, "hisAnnouncement/query", data=form)
            result["provenance"]["queries"].append(proof)
            rows = _rows(payload, symbol, issuer["orgId"])
            if payload["hasMore"]:
                result["missing"].append(f"{query_name}检索尚有后续页；本次只取前30条，不宣称完整或已找到全站最新。")
            eligible = []
            for row in rows:
                title = row.get("announcementTitle", "")
                if "摘要" in title or "英文" in title or "取消" in title:
                    continue
                if not (_REPORT.search(title) if query_name == "reports" else _NOTICE.search(title)):
                    continue
                published, precision, claimed = _publication(row)
                if published > as_of:
                    result["missing"].append("部分披露仅日精度且保守可用上界晚于as_of，暂未采纳；不倒填为午夜可见。")
                    continue
                eligible.append((published, row, precision, claimed))
            eligible.sort(key=lambda v: (v[0], v[1]["announcementId"]), reverse=True)
            if eligible:
                selected.append(eligible[0])
            elif query_name == "reports":
                result["missing"].append("本次检索未找到可验证的定期报告全文；不代表公司没有业务。")
        document_failed = False
        for published, row, precision, claimed in selected:
            if any(d["announcement_id"] == row["announcementId"] for d in result["documents"]):
                continue
            try:
                path, receipt = _original(client, root, row)
                parsed = _parse_pdf(path)
                company, identity_page, identity_quote = _identity(parsed["pages"], symbol)
                if result["company_name"] not in (None, company):
                    raise SourceError("issuer legal name changed between documents; needs explicit reconciliation")
                result["company_name"] = company
                excerpts, missing = _excerpts(parsed["pages"])
                result["missing"].extend(missing)
                if parsed.get("truncated"):
                    result["missing"].append("PDF文本抽取达到页数/文本预算；只覆盖所给候选句。")
                if not excerpts:
                    raise SourceError("no complete extractable business sentences; scanned/empty/unsupported PDF")
                result["documents"].append({"id": "cninfo-" + row["announcementId"], "symbol": symbol,
                    "company_name": company, "title": row["announcementTitle"],
                    "announcement_id": row["announcementId"], "org_id": issuer["orgId"],
                    "url": receipt["url"], "published_at": published.isoformat(),
                    "published_precision": precision, "source_claimed_at": claimed,
                    "received_at": receipt["received_at"], "period": _period(row["announcementTitle"]),
                    "sha256": receipt["sha256"], "identity_page": identity_page,
                    "identity_quote": identity_quote, "excerpts": excerpts,
                    "extraction_truncated": bool(parsed.get("truncated")),
                    "extraction_method": EXTRACTOR,
                    "parser_memory_limit_mode": parsed.get("memory_limit_mode", "unknown")})
            except (SourceError, OSError, ValueError) as error:
                document_failed = True
                result["missing"].append(f"公告{row['announcementId']}采集/提取失败：{_failure(error)}")
        result["status"] = "ready" if result["documents"] else ("failed" if document_failed else "missing")
        # The old consumer caps the company packet, not each document, at twelve.
        documents = result["documents"]
        retained = [[] for _ in documents]
        for offset in range(12):
            for index, doc in enumerate(documents):
                if offset < len(doc["excerpts"]) and sum(map(len, retained)) < 12:
                    retained[index].append(doc["excerpts"][offset])
        for doc, excerpts in zip(documents, retained):
            if len(excerpts) < len(doc["excerpts"]):
                result["missing"].append("每公司总12片段预算截断，未覆盖全部候选句。")
                doc["extraction_truncated"] = True
            doc["excerpts"] = excerpts
    except (SourceError, httpx.HTTPError, OSError, ValueError, TypeError, KeyError) as error:
        result["status"] = "failed"
        result["missing"].append(f"公司披露来源失败：{_failure(error)}")
    finally:
        if owned:
            client.close()
    result["checked_at"] = _now().isoformat()
    hours = 6 if result["status"] == "ready" else (0.5 if result["status"] == "failed" else 1)
    result["cache_until"] = (_now() + timedelta(hours=hours)).isoformat()
    result["missing"] = list(dict.fromkeys(result["missing"]))
    if cache_error is None:  # Preserve conflicted cache and its recovery evidence.
        _atomic(cache, result)
    return result


def _worker(path: str, output: str) -> None:
    import resource

    # No inherited credentials/config/PYTHONPATH. Reject instead of falling back
    # to in-process parsing when resource limits/dependency are unavailable.
    memory_caps = []
    for name in ("RLIMIT_AS", "RLIMIT_DATA"):
        try:
            resource.setrlimit(getattr(resource, name), (768 * 1024 * 1024, 768 * 1024 * 1024))
            memory_caps.append(name)
        except (OSError, ValueError):
            pass  # Parent RSS watchdog remains mandatory on these platforms.
    for name, limit in (("RLIMIT_CPU", 22), ("RLIMIT_FSIZE", 2 * MAX_JSON)):
        resource.setrlimit(getattr(resource, name), (limit, limit))
    from pypdf import PdfReader

    pdf = PdfReader(path)
    if pdf.is_encrypted or len(pdf.pages) > 350:
        raise SourceError("encrypted/oversized PDF")
    pages, remaining, truncated = [], MAX_JSON // 2, len(pdf.pages) > 70
    for page in pdf.pages[:70]:
        text = page.extract_text() or ""
        if len(text) > 12000 or len(text) > remaining:
            truncated = True
        text = text[:min(12000, remaining)]
        pages.append(text)
        remaining -= len(text)
        if remaining <= 0:
            break
    Path(output).write_text(json.dumps({"pages": pages, "truncated": truncated,
        "memory_limit_mode": "+".join(memory_caps + ["parent_sampled_rss_768MiB"])},
        ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] != "--parse":
        raise SystemExit("internal bounded PDF parser")
    _worker(sys.argv[2], sys.argv[3])
