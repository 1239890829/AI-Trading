import io
import json
import struct
import zipfile
from datetime import date

import pytest

from app.market.tdx_daily_package import archive_package, parse_package, verify_archive

DAY=date(2026,9,30)


def package(*, duplicate=False, missing=False, bad_price=False):
    buf=io.BytesIO()
    with zipfile.ZipFile(buf,'w',zipfile.ZIP_DEFLATED) as z:
        for market,codes in [('sh',['600519','688981','510300','000001']),('sz',['000001','300750']),('bj',['920001'])]:
            if missing and market=='bj':continue
            cod,md=bytearray(),bytearray()
            for n,code in enumerate(codes):
                rec=bytearray(150);rec[:6]=code.encode();struct.pack_into('<H',rec,32,0 if duplicate else n)
                name='证券'.encode('gbk');rec[40:40+len(name)]=name;cod.extend(rec)
                block=bytearray(512)
                struct.pack_into('<d',block,4,10)
                struct.pack_into('<4d',block,12,10,11,9,float('nan') if bad_price else 10)
                struct.pack_into('<Q',block,56,1000)
                struct.pack_into('<d',block,72,10000);md.extend(block)
            for suffix, content in [('cod',cod),('md1',md)]:
                info=zipfile.ZipInfo(market+'260930.'+suffix, (2026,9,30,15,0,0))
                info.compress_type=zipfile.ZIP_DEFLATED
                z.writestr(info,content)
    return buf.getvalue()


def test_package_excludes_index_etf_and_keeps_star_shares():
    rows,markets=parse_package(package(),DAY)
    assert len(rows)==5
    assert not any(r['symbol']=='510300' or r['market']=='SH' and r['symbol']=='000001' for r in rows)
    assert next(r for r in rows if r['symbol']=='688981')['volume']==1000
    assert markets['sh']=={'package_records':4,'priced_ashare_records':2}
    assert all(r['adjustment']=='none' and r['amount_unit']=='CNY' for r in rows)


@pytest.mark.parametrize('kwargs',[{'duplicate':True},{'missing':True},{'bad_price':True}])
def test_corrupt_or_partial_market_rejected(kwargs):
    with pytest.raises(ValueError):parse_package(package(**kwargs),DAY)


def test_source_day_mismatch_rejected():
    with pytest.raises(ValueError,match='missing market/date'):
        parse_package(package(),date(2026,9,29))


def test_atomic_archive_idempotent_and_hash_verification(tmp_path):
    manifest=archive_package(package(),DAY,tmp_path)
    assert archive_package(package(),DAY,tmp_path)==manifest
    folder=tmp_path/'20260930'
    assert verify_archive(folder)==manifest
    meta=json.loads((folder/'manifest.json').read_text());meta['rows']=999
    (folder/'manifest.json').write_text(json.dumps(meta))
    with pytest.raises(ValueError,match='manifest mismatch'):verify_archive(folder)


def test_failed_write_leaves_no_published_partial_archive(tmp_path,monkeypatch):
    from app.market import tdx_daily_package as mod
    def broken(*args):raise OSError('disk full')
    monkeypatch.setattr(mod,'write_parquet_atomic',broken)
    with pytest.raises(OSError):archive_package(package(),DAY,tmp_path)
    assert not list(tmp_path.iterdir())
