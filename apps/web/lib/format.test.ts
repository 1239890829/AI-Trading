/** 格式化工具测试：空值/边界/单位换算/红涨绿跌语义。 */
import { describe, expect, it } from "vitest";
import { fmt, fmtAmount, fmtVolume, parseNum, pctColor, pctText, qualityLabel, sourceLabel, timeText } from "./format";

describe("fmt", () => {
  it("null/undefined/NaN → --", () => {
    expect(fmt(null)).toBe("--");
    expect(fmt(undefined)).toBe("--");
    expect(fmt(Number.NaN)).toBe("--");
  });

  it("thousands separator and fixed digits", () => {
    expect(fmt(1297.4)).toBe("1,297.40");
    expect(fmt(1234567.891, 0)).toBe("1,234,568");
  });
});

describe("fmtAmount", () => {
  it("亿/万/auto unit switch", () => {
    expect(fmtAmount(2.1e9)).toBe("21.00 亿");
    expect(fmtAmount(5.2e5)).toBe("52.00 万");
    expect(fmtAmount(999)).toBe("999");
  });

  it("negative amounts keep sign", () => {
    expect(fmtAmount(-2.5e8)).toContain("-2.50 亿");
  });
});

describe("fmtVolume", () => {
  it("converts shares to lots", () => {
    expect(fmtVolume(83880000)).toBe("838,800");
  });
});

describe("pctColor / pctText", () => {
  it("red up, green down (A-share convention)", () => {
    expect(pctColor(1.5)).toBe("text-up");
    expect(pctColor(-1.5)).toBe("text-down");
    expect(pctColor(0)).toBe("text-zinc-400");
  });

  it("pctText keeps explicit plus and handles null", () => {
    expect(pctText(1.5)).toBe("+1.50%");
    expect(pctText(-1.5)).toBe("-1.50%");
    expect(pctText(null)).toBe("--");
  });
});

describe("sourceLabel / qualityLabel / timeText", () => {
  it("provider keys never leak raw (project convention)", () => {
    expect(sourceLabel("tencent")).toBe("腾讯");
    expect(sourceLabel("ths")).toBe("同花顺");
    expect(sourceLabel(null)).toBe("--");
    expect(sourceLabel("unknown")).toBe("unknown"); // 未知源原样透传
  });

  it("quality levels map to Chinese", () => {
    expect(qualityLabel("high")).toBe("正常");
    expect(qualityLabel("stale")).toBe("过期");
  });

  it("invalid time string → --", () => {
    expect(timeText("not-a-date")).toBe("--");
    expect(timeText(null)).toBe("--");
  });
});

describe("parseNum", () => {
  it("strips thousands separators (regression: parseFloat 会在逗号处截断)", () => {
    // fmt(1297.4) === "1,297.40"；直接 parseFloat 得到 1，会让下单价变成 1 元
    expect(parseNum(fmt(1297.4))).toBe(1297.4);
    expect(parseNum("1,234,568")).toBe(1234568);
    expect(parseNum("12,345,678.90")).toBe(12345678.9);
  });

  it("handles plain numbers, empty and garbage", () => {
    expect(parseNum(42)).toBe(42);
    expect(parseNum("39.8")).toBe(39.8);
    expect(parseNum("")).toBe(0);
    expect(parseNum(null)).toBe(0);
    expect(parseNum(undefined)).toBe(0);
    expect(parseNum("abc")).toBe(0);
    expect(parseNum(Number.NaN)).toBe(0);
  });
});
