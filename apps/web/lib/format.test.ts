/** 格式化工具测试：空值/边界/单位换算/红涨绿跌语义。 */
import { describe, expect, it } from "vitest";
import { fmt, fmtAmount, fmtHeat, fmtVolume, isHardQuality, parseNum, pctColor, pctText, qualityLabel, sourceLabel, timeText, timeTextUTC, dateTimeTextUTC, bjDate, bjHHMM, bjMonthDay, triText } from "./format";

/**
 * 北京时间格式化（2026-09-11 收口）。
 *
 * 这组用例的价值不在"能格式化"，而在**钉住时区与空值语义**——两者都是静默出错：
 * 时区错了会整体错一天（CI 容器非 +8 时区时最明显），空值语义错了会让两个非法
 * 时间戳"相等"从而误判同分钟（kline-live 的实时合成缺陷）。
 */
describe("北京时间格式化", () => {
  it("按 Asia/Shanghai 渲染，与运行环境时区无关", () => {
    // 2026-09-11T01:20:00Z = 北京 09:20（开盘竞价）——用默认时区渲染会变成 01:20
    expect(bjHHMM("2026-09-11T01:20:00Z")).toBe("09:20");
    expect(bjDate("2026-09-11T01:20:00Z")).toBe("2026-09-11");
    expect(bjMonthDay("2026-09-11T01:20:00Z")).toBe("09-11");
  });

  it("跨日边界：UTC 前一日深夜属北京次日", () => {
    // 2026-09-10T16:00:00Z = 北京 2026-09-11 00:00
    expect(bjDate("2026-09-10T16:00:00Z")).toBe("2026-09-11");
    expect(bjHHMM("2026-09-10T16:00:00Z")).toBe("00:00");
    // 再早一秒仍是前一日
    expect(bjDate("2026-09-10T15:59:59Z")).toBe("2026-09-10");
  });

  it("收盘时刻：15:00 北京时间", () => {
    expect(bjHHMM("2026-09-11T07:00:00Z")).toBe("15:00");
  });

  it("缺失/非法一律返回空串（不得产出 'Invalid Date' 类垃圾串）", () => {
    for (const bad of [null, undefined, "", "not-a-date"]) {
      expect(bjHHMM(bad)).toBe("");
      expect(bjDate(bad)).toBe("");
      expect(bjMonthDay(bad)).toBe("");
    }
    // 关键回归：两个不同的非法输入必须"相等"于空串，而不是等于同一段垃圾串后
    // 被上游当作"同一分钟"从而跳过实时合成
    expect(bjHHMM("garbage-a")).toBe(bjHHMM("garbage-b"));
    expect(bjHHMM("garbage-a")).toBe("");
  });

  it("输入带 Z 的 UTC ISO（后端统一格式）", () => {
    expect(bjHHMM("2026-09-11T03:47:12.345Z")).toBe("11:47");
    expect(bjDate("2026-09-11T03:47:12.345Z")).toBe("2026-09-11");
  });
});


describe("isHardQuality", () => {
  it("低/中质量（盘中瞬态）不渲染徽标，持久态（过期/非法）才渲染", () => {
    // low/medium 来自盘中源字段瞬时不同步，下一个 tick 即恢复——上界面就闪
    expect(isHardQuality("high")).toBe(false);
    expect(isHardQuality("low")).toBe(false);
    expect(isHardQuality("medium")).toBe(false);
    // stale/invalid 是持久态，稳定显示不闪
    expect(isHardQuality("stale")).toBe(true);
    expect(isHardQuality("invalid")).toBe(true);
    expect(isHardQuality("unknown-future")).toBe(false);
  });
});

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

describe("fmtHeat", () => {
  it("compacts heat counts with 万/亿 (ths 人气口径)", () => {
    // 真实样本：2026-08-31 实抓 榜首 heat="6002184"（接口为字符串，后端已转数值）
    expect(fmtHeat(6002184)).toBe("600.2万");
    expect(fmtHeat(1.2e8)).toBe("1.20亿");
    expect(fmtHeat(999)).toBe("999");
    expect(fmtHeat(null)).toBe("--");
  });
});

describe("pctColor / pctText", () => {
  it("red up, green down (A-share convention)", () => {
    expect(pctColor(1.5)).toBe("text-up-ink dark:text-up");
    expect(pctColor(-1.5)).toBe("text-down-ink dark:text-down");
    expect(pctColor(0)).toBe("text-zinc-600 dark:text-zinc-400");
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

/**
 * 三态文案（S2-9，2026-09-11）。
 *
 * 价值在于**跨端一致**：这组键必须与后端 `picks/push_cards.py::_TRI_LABELS` 逐键对齐，
 * 否则同一份数据在飞书卡片与界面上显示不同（此前前端漏 `null` 键 ⇒ 界面直接打出 `null`）。
 * 同时钉住"判不出（unknown）≠ 没有值（--）"这条三态纪律。
 */
describe("三态文案 triText", () => {
  it("三个字面量都有中文文案，且与后端逐键一致", () => {
    expect(triText("unknown")).toBe("未判定");
    expect(triText("none")).toBe("无");
    expect(triText("null")).toBe("—");
  });

  it("大小写不敏感、两侧空白不影响", () => {
    expect(triText("UNKNOWN")).toBe("未判定");
    expect(triText("  None  ")).toBe("无");
    expect(triText("NULL")).toBe("—");
  });

  it("真正缺数据 → '--'（与字面量 'null' 区分：前者没有值，后者值是这四个字符）", () => {
    expect(triText(null)).toBe("--");
    expect(triText(undefined)).toBe("--");
    expect(triText("")).toBe("--");
    expect(triText("   ")).toBe("--");
  });

  it("未登记的字面量原样透出（不臆造翻译）", () => {
    expect(triText("strong")).toBe("strong");
  });
});

/** agent 域时间戳是**无时区的 UTC naive**——这是「交易智能体显示早 8 小时」的根因。 */
describe("timeTextUTC / dateTimeTextUTC", () => {
  it("把无时区的 UTC 串按 UTC 解释（07:45 UTC → 15:45 北京）", () => {
    // 若按本地时区（+8）解释，会得到 07:45 ⇒ 整整早 8 小时
    expect(timeTextUTC("2026-09-11T07:45:06.541226")).toContain("15:45");
    expect(dateTimeTextUTC("2026-09-11T07:45:06.541226")).toContain("15:45");
  });

  it("已带 Z 的串不受影响（不会被二次偏移）", () => {
    expect(timeTextUTC("2026-09-11T07:45:06Z")).toContain("15:45");
  });

  it("带偏移的串原样处理", () => {
    expect(timeTextUTC("2026-09-11T15:45:00+08:00")).toContain("15:45");
  });

  it("跨零点正确（UTC 16:00 → 北京次日 00:00）", () => {
    // 注意：zh-CN 的 toLocaleString 用斜杠分隔（09/12），与后端日期串的短横线不同
    expect(dateTimeTextUTC("2026-09-11T16:00:00")).toContain("09/12");
  });

  it("空值与非法值返回 --", () => {
    expect(timeTextUTC(null)).toBe("--");
    expect(timeTextUTC("")).toBe("--");
    expect(timeTextUTC("not a date")).toBe("--");
    expect(dateTimeTextUTC(undefined)).toBe("--");
  });

  it("输出不受运行环境时区影响（固定 Asia/Shanghai）", () => {
    // 与 timeText 的差别：timeText 未指定 timeZone，在非 +8 环境会错
    const out = timeTextUTC("2026-09-11T07:45:06");
    expect(out).toContain("15:45");
    expect(out).not.toContain("07:45");
  });
});
