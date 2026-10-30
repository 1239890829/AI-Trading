import {beforeEach, describe, expect, it, vi} from "vitest";
import {deleteUsWatchRecord, getUsAssetRecords, getUsSnapshot, saveUsWatchRecord, searchUsAssets, usInstrumentMatches, validUsSourceSymbol, type UsAssetRecord, type UsInstrument} from "@/lib/api/us-assets";
import {getJson, sendJson} from "@/lib/api/internal";

vi.mock("@/lib/api/internal", () => ({getJson: vi.fn(), sendJson: vi.fn()}));
const apple: UsInstrument = {instrument_id: "us_equity:US:AAPL.OQ:USD", asset_type: "us_equity", market: "US", symbol: "AAPL", source_symbol: "AAPL.OQ", currency: "USD", name: "苹果", english_name: "Apple Inc.", security_type: "stock", exchange_source_code: "OQ", source: "tencent"};
beforeEach(() => {vi.resetAllMocks(); vi.mocked(getJson).mockResolvedValue({data: {items: []}, meta: {} as never}); vi.mocked(sendJson).mockResolvedValue({data: {}, meta: {} as never});});

describe("美股请求身份、用途与恢复契约", () => {
  it("查询独立美股域和记录scope，传播取消信号且不进入A股API", async () => {
    const signal = new AbortController().signal;
    await getUsSnapshot("AAPL.OQ", {signal}); await getUsAssetRecords({signal}); await searchUsAssets("苹果 AAPL", {signal});
    expect(vi.mocked(getJson).mock.calls.map(call => call[0])).toEqual(["/api/assets/us/AAPL.OQ/snapshot?limit=120", "/api/assets/records?asset_type=us_equity", "/api/assets/search?q=%E8%8B%B9%E6%9E%9C+AAPL&asset_type=us_equity&limit=20"]);
    expect(vi.mocked(getJson).mock.calls.every(call => call[2]?.signal === signal)).toBe(true);
  });
  it("自选只写完整来源身份、空持有字段和CAS版本，保留旧标签与备注", async () => {
    const previous = {instrument_id: apple.instrument_id, asset_type: "us_equity", source_symbol: apple.source_symbol, currency: "USD", name: "保存名称", units: null, cost_total: null, note: "原备注", version: 4} as UsAssetRecord;
    await saveUsWatchRecord(apple, 4, previous);
    expect(sendJson).toHaveBeenCalledWith("/api/assets/records/us_equity%3AUS%3AAAPL.OQ%3AUSD", "PUT", {source_symbol: "AAPL.OQ", name: "保存名称", is_watched: true, units: null, cost_total: null, note: "原备注", expected_version: 4}, 45_000);
    await deleteUsWatchRecord(apple.instrument_id, 4);
    expect(sendJson).toHaveBeenLastCalledWith("/api/assets/records/us_equity%3AUS%3AAAPL.OQ%3AUSD?expected_version=4", "DELETE");
  });
  it("不同市场/币种/股票不通过同一美股身份，不猜交易所后缀", () => {
    expect(usInstrumentMatches(apple)).toBe(true); expect(validUsSourceSymbol("BRK.B.N")).toBe(true); expect(validUsSourceSymbol("SPY.AM")).toBe(true);
    for (const value of ["AAPL", "AAPL.OF", "600519.SH", "NVDA.SZ", "AAPL.OQ?other=1", "AAPL.HK", "AAPL.P", "AAPL.A", "AAPL.FAKE"]) expect(validUsSourceSymbol(value)).toBe(false);
    expect(usInstrumentMatches({...apple, symbol: "NVDA"})).toBe(false);
    expect(usInstrumentMatches({...apple, currency: "CNY"} as unknown as UsInstrument)).toBe(false);
    expect(usInstrumentMatches({...apple, source_symbol: "AAPL.N"})).toBe(false);
  });
  it("错误身份与旧持有字段不能被只读自选覆盖，也不能删除基金记录", async () => {
    await expect(saveUsWatchRecord({...apple, symbol: "NVDA"}, 0)).rejects.toThrow("身份");
    await expect(saveUsWatchRecord(apple, 4, {...apple, units: 2, cost_total: 200} as unknown as UsAssetRecord)).rejects.toThrow("旧记录");
    await expect(deleteUsWatchRecord("fund:OF:040046:USD", 4)).rejects.toThrow("身份");
    await expect(deleteUsWatchRecord(apple.instrument_id, 0)).rejects.toThrow("版本");
    expect(sendJson).not.toHaveBeenCalled();
  });
});
