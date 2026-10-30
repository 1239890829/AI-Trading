import {beforeEach, describe, expect, it, vi} from "vitest";
import {deleteAssetRecord, getAssetCapabilities, getAssetRecords, getFundCatalog, getFundSnapshot, saveAssetRecord, searchAssets} from "@/lib/api/assets";
import {getJson, sendJson} from "@/lib/api/internal";
import {assetWorkbenchUrl, fundIdentityMatches} from "@/lib/asset-identity";

vi.mock("@/lib/api/internal", () => ({getJson: vi.fn(), sendJson: vi.fn()}));
beforeEach(() => {vi.resetAllMocks(); vi.mocked(getJson).mockResolvedValue({data: {items: []}, meta: {} as never}); vi.mocked(sendJson).mockResolvedValue({data: {}, meta: {} as never});});

describe("资产请求与跨市场身份合同", () => {
  it("全部读取走资产域、完整OF代码和AbortSignal，不进入A股行情API", async () => {
    const signal = new AbortController().signal;
    await getAssetCapabilities({signal}); await searchAssets("纳指 联接", "fund", {signal}); await getFundSnapshot("040046.OF", "month", {signal}); await getAssetRecords({signal}); await getFundCatalog("source_buyable", {signal});
    expect(vi.mocked(getJson).mock.calls.map(call => call[0])).toEqual(["/api/assets/capabilities", "/api/assets/search?q=%E7%BA%B3%E6%8C%87+%E8%81%94%E6%8E%A5&asset_type=fund&limit=20", "/api/assets/funds/040046.OF/snapshot?range=month", "/api/assets/records?asset_type=fund", "/api/assets/funds/catalog?tab=meiguo&purchase=source_buyable"]);
    expect(vi.mocked(getJson).mock.calls.every(call => call[2]?.signal === signal)).toBe(true);
  });
  it("持有写入带完整资产币种与版本，不写paper或real账户", async () => {
    const input = {source_symbol: "040046.OF", is_watched: true, units: 12.5, cost_total: 31, expected_version: 4};
    await saveAssetRecord("fund:OF:040046:USD", input); await deleteAssetRecord("fund:OF:040046:USD", 4);
    expect(sendJson).toHaveBeenNthCalledWith(1, "/api/assets/records/fund%3AOF%3A040046%3AUSD", "PUT", input, 45_000);
    expect(sendJson).toHaveBeenNthCalledWith(2, "/api/assets/records/fund%3AOF%3A040046%3AUSD?expected_version=4", "DELETE");
  });
  it("同号A股、不同币种、错误市场不能混成一个基金身份", () => {
    const value = {instrument_id: "fund:OF:040046:USD", source_symbol: "040046.OF", asset_type: "fund", market: "OF", currency: "USD", symbol: "040046"};
    expect(fundIdentityMatches(value)).toBe(true);
    expect(fundIdentityMatches({...value, currency: "CNY"})).toBe(false);
    expect(fundIdentityMatches({...value, source_symbol: "040046"})).toBe(false);
    expect(fundIdentityMatches({...value, market: "SH"})).toBe(false);
    expect(assetWorkbenchUrl("fund", value.source_symbol)).toBe("/workbench?mode=watch&asset=fund&symbol=040046.OF");
    expect(assetWorkbenchUrl("cn_equity", "600519")).toBe("/workbench?mode=watch&symbol=600519");
  });
});
