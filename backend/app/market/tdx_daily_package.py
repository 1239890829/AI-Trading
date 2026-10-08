"""TDX官网历史日线包：离线A股归档，原价/股/元，不写在线marketdb。

公开格式参照 a-stock-data V3.10.1 与 jing2uo/tdx2db：
.cod 每记录150字节，序号32:34；.md1 每块512字节。
仅解码内存内已知成员；不执行第三方代码、不提取zip路径。
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import shutil
import struct
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from time import monotonic

import httpx

from app.services.parquet_store import write_parquet_atomic

URL = 'https://www.tdx.com.cn/products/data/data/g4day/{ymd}.zip'
MAX_COMPRESSED = 8 * 1024 * 1024
MAX_EXPANDED = 80 * 1024 * 1024


def is_ashare(market: str, code: str) -> bool:
    if market == 'sh':
        return code.startswith(('600','601','603','605','688','689'))
    if market == 'sz':
        return code.startswith(('000','001','002','003','300','301'))
    return market == 'bj' and code.startswith(('43','83','87','88','92'))


def parse_package(raw: bytes, day: date) -> tuple[list[dict], dict]:
    if len(raw) > MAX_COMPRESSED:
        raise ValueError('TDX compressed package exceeds size limit')
    ymd = day.strftime('%Y%m%d')
    rows, markets = [], {}
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        infos = archive.infolist()
        names = [x.filename for x in infos]
        if len(names) != len(set(names)) or sum(x.file_size for x in infos) > MAX_EXPANDED:
            raise ValueError('TDX duplicate archive member or expanded size limit')
        for market in ('sh','sz','bj'):
            cod_name, md_name = f'{market}{ymd[2:]}.cod', f'{market}{ymd[2:]}.md1'
            if day < date(2022,5,6) and market == 'bj' and cod_name not in names and md_name not in names:
                continue
            if cod_name not in names or md_name not in names:
                raise ValueError(f'TDX missing market/date members: {market}/{ymd}')
            cod, md = archive.read(cod_name), archive.read(md_name)
            if not cod or len(cod)%150 or len(md)%512 or len(cod)//150 != len(md)//512:
                raise ValueError(f'TDX truncated/mismatched blocks: {market}')
            codes, seqs = set(), set()
            selected = 0
            for off in range(0,len(cod),150):
                rec = cod[off:off+150]
                code = rec[:6].decode('ascii')
                seq = struct.unpack_from('<H',rec,32)[0]
                if not code.isdigit() or code in codes or seq in seqs or seq*512+512 > len(md):
                    raise ValueError(f'TDX invalid/duplicate identity or offset: {market}')
                codes.add(code);seqs.add(seq)
                if not is_ashare(market,code):
                    continue
                block = md[seq*512:(seq+1)*512]
                prev = struct.unpack_from('<d',block,4)[0]
                open_, high, low, close = struct.unpack_from('<4d',block,12)
                vol = struct.unpack_from('<Q',block,56)[0]
                amount = struct.unpack_from('<d',block,72)[0]
                if not all(math.isfinite(n) and n>=0 for n in (prev,open_,high,low,close,amount)):
                    raise ValueError(f'TDX invalid prices/amount: {market}{code}')
                if close == 0:
                    continue  # 无当日价格不伪造OHLC；manifest保留筛选分母
                if high and (high < low or high < close or high < open_) or low and (low > close or open_ and low > open_):
                    raise ValueError(f'TDX inconsistent OHLC: {market}{code}')
                name = rec[40:72].split(b'\0',1)[0].decode('gbk').strip()
                if not name:
                    raise ValueError(f'TDX missing name: {market}{code}')
                rows.append({'symbol':code,'market':market.upper(),'trade_date':day.isoformat(),'name':name,'prev_close':prev,'open':open_,'high':high,'low':low,'close':close,'volume':vol,'amount':amount,'source':'tdx_daily_package','adjustment':'none','volume_unit':'shares','amount_unit':'CNY'})
                selected += 1
            if selected == 0:
                raise ValueError(f'TDX no priced A shares in {market}; refuse partial archive')
            markets[market] = {'package_records':len(codes),'priced_ashare_records':selected}
    return rows, markets


def archive_package(raw: bytes, day: date, root: Path, *, received_at: datetime | None = None) -> dict:
    """单目录原子发布：Parquet、原文zip、manifest必须一起可见。日期归档不可静默改写。"""
    import polars as pl

    rows, markets = parse_package(raw, day)
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    target = root / day.strftime('%Y%m%d')
    digest = hashlib.sha256(raw).hexdigest()
    if target.exists():
        manifest = verify_archive(target)
        if manifest['raw_sha256'] != digest:
            raise ValueError('TDX archived source revised; preserve old archive and review revision separately')
        return manifest
    temp = Path(tempfile.mkdtemp(prefix='.tdx-', dir=root))
    try:
        (temp/'source.zip').write_bytes(raw)
        write_parquet_atomic(pl.DataFrame(rows), temp/'daily.parquet')
        manifest = {'source':'tdx_daily_package','source_url':URL.format(ymd=day.strftime('%Y%m%d')),'trade_date':day.isoformat(),'received_at':received_at.isoformat() if received_at else None,'archived_at':datetime.now(timezone.utc).isoformat(),'raw_sha256':digest,'parquet_sha256':hashlib.sha256((temp/'daily.parquet').read_bytes()).hexdigest(),'rows':len(rows),'markets':markets,'adjustment':'none','scope':'priced A-share code prefixes; not a verified historical listing universe','point_in_time':False}
        (temp/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
        os.rename(temp, target)
        return manifest
    finally:
        if temp.exists():
            shutil.rmtree(temp)


def verify_archive(folder: Path) -> dict:
    import polars as pl

    manifest = json.loads((folder/'manifest.json').read_text())
    for filename, key in [('source.zip','raw_sha256'),('daily.parquet','parquet_sha256')]:
        if hashlib.sha256((folder/filename).read_bytes()).hexdigest() != manifest[key]:
            raise ValueError(f'TDX archive integrity mismatch: {filename}')
    rows, markets = parse_package((folder/'source.zip').read_bytes(), date.fromisoformat(manifest['trade_date']))
    frame = pl.read_parquet(folder/'daily.parquet')
    if frame.to_dicts() != rows or manifest['rows'] != len(rows) or manifest['markets'] != markets:
        raise ValueError('TDX archive data/manifest mismatch')
    return manifest


def download_package(day: date) -> bytes:
    url = URL.format(ymd=day.strftime('%Y%m%d'))
    deadline = monotonic() + 60
    with httpx.Client(trust_env=False, timeout=10, follow_redirects=False) as client:
        with client.stream('GET',url) as resp:
            if resp.status_code == 404:
                raise ValueError('TDX package unavailable: unpublished, closed day, or outside retention; not an empty market')
            resp.raise_for_status()
            chunks, size = [], 0
            for chunk in resp.iter_bytes():
                if monotonic() > deadline:
                    raise TimeoutError('TDX download overall time budget exceeded')
                size += len(chunk)
                if size > MAX_COMPRESSED:
                    raise ValueError('TDX download exceeds size limit')
                chunks.append(chunk)
    return b''.join(chunks)
