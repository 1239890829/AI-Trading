/** 格式化工具测试：空值/边界/单位换算/红涨绿跌语义。 */
import { describe, expect, it } from "vitest";
import { fmt, fmtAmount, fmtHeat, fmtVolume, isHardQuality, parseNum, pctColor, pctText, qualityLabel, shouldShowQualityBadge, sourceLabel, timeText, timeTextBJ, dateTimeTextBJ, bjDate, bjHHMM, bjMonthDay, triAmount, triText, winRateColor } from "./format";

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

describe("shouldShowQualityBadge（徽标可见性策略的唯一决策点）", () => {
  it("只压 low/medium 两档瞬态；high 必须渲染（三态可辨：正常 ≠ 字段缺失）", () => {
    // 09-02 的降噪目标只有 low/medium；high 被一起隐掉是副作用 ⇒ 2026-09-14 归位
    expect(shouldShowQualityBadge("high")).toBe(true);
    expect(shouldShowQualityBadge("stale")).toBe(true);
    expect(shouldShowQualityBadge("invalid")).toBe(true);
  });

  it("low/medium 不渲染（闪烁来源，2026-09-02 修复对象）", () => {
    expect(shouldShowQualityBadge("low")).toBe(false);
    expect(shouldShowQualityBadge("medium")).toBe(false);
  });

  it("缺失/空串不渲染；未知档位**渲染**（宁可多显示异常，不可静默吞掉新档位）", () => {
    expect(shouldShowQualityBadge(null)).toBe(false);
    expect(shouldShowQualityBadge(undefined)).toBe(false);
    expect(shouldShowQualityBadge("")).toBe(false);
    expect(shouldShowQualityBadge("brand-new-level")).toBe(true);
  });

  it("与 isHardQuality 是**两个不同问题**，不可互相替代", () => {
    // 这是本轮缺陷的根因：把「是否硬质量问题」当成「是否渲染徽标」用了
    expect(isHardQuality("high")).toBe(false);
    expect(shouldShowQualityBadge("high")).toBe(true);
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

// 胜率着色的**三态**守卫（2026-09-14 审查批次 B5）。
//
// 被守的缺陷：旧写法 `(rate ?? 0) >= 50` 把"样本不足 / 未到期"当成 0% ⇒ 染跌色，
// 而同一容器里的文案正写着「样本不足」——**颜色把"没数据"说成"表现差"**，两者
// 自相矛盾。本仓「三态 > 二态」纪律要求缺失显式为未判定（中性色），与 pctColor
// 对 null 的处理同源。
//
// 另一处被钉住的是**口径必须显式**：本仓胜率两套量纲并存（signal-health / 角色
// 胜率 = 0-1 小数；intraday-review / 盘后复盘 = 0-100 百分数），因此 `scale`
// 刻意不设默认值；最后一条用例直接证明混用会翻转结论。
describe("winRateColor（胜率三态着色）", () => {
  const NEUTRAL = "text-zinc-600 dark:text-zinc-400";
  const UP = "text-up-ink dark:text-up";
  const DOWN = "text-down-ink dark:text-down";

  it("缺失一律中性——「样本不足」不是负面色（三态纪律）", () => {
    expect(winRateColor(null, "pct")).toBe(NEUTRAL);
    expect(winRateColor(undefined, "01")).toBe(NEUTRAL);
    expect(winRateColor(NaN, "pct")).toBe(NEUTRAL);
  });

  it("pct 口径（0-100）：>= 50 正向", () => {
    expect(winRateColor(50, "pct")).toBe(UP);
    expect(winRateColor(49.9, "pct")).toBe(DOWN);
    expect(winRateColor(0, "pct")).toBe(DOWN);
  });

  it("01 口径（0-1）：>= 0.5 正向，与 pct 同一天花板", () => {
    expect(winRateColor(0.5, "01")).toBe(UP);
    expect(winRateColor(0.499, "01")).toBe(DOWN);
    expect(winRateColor(1, "01")).toBe(UP);
  });

  it("两口径不可混用——同一数字换口径结论相反", () => {
    // 0.55 在 01 口径下是 55%（正向）；误当 pct 口径则被读成 0.55%（负向）。
    // 这正是 `scale` 不设默认值的原因：让口径差异在调用点可见。
    expect(winRateColor(0.55, "01")).toBe(UP);
    expect(winRateColor(0.55, "pct")).toBe(DOWN);
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

/**
 * 成交额三态渲染（2026-09-14）。
 *
 * 起因是一次真实报障：「两市成交额怎么没出来了」。根因是后端重启触发新浪 WAF
 * 限流（HTTP 456）⇒ 全市场快照约 6 分钟不就绪 ⇒ `total_amount = null`，
 * 而两处消费点都写成 `totalAmount ? fmtAmount(totalAmount) : "--"` ——
 * **把「尚未就绪」和「真的没有」显示成同一个 `--`**，用户无从分辨是加载中还是坏了。
 */
describe("triAmount（成交额三态渲染）", () => {
  it("有值 → 金额（与 fmtAmount 同口径）", () => {
    expect(triAmount(1.2345e11, "ready")).toBe(fmtAmount(1.2345e11));
    // 0 是合法值，不得被真值判断吞成"无"
    expect(triAmount(0, "ready")).toBe(fmtAmount(0));
  });

  it("未就绪 → 「加载中…」而不是 '--'（本次报障要修的就是这一格）", () => {
    expect(triAmount(null, "unavailable")).toBe("加载中…");
  });

  it("unknown → 「未判定」（不得与 unavailable 合并显示）", () => {
    // 2026-09-14 IMP-002 修正：原实现把 unknown 也渲染成「加载中…」，
    // 与 lib/api.ts docstring 自述纪律（「unknown 显式「未判定」」）及 triText 的映射矛盾
    // ——同一份数据在飞书卡片显示「未判定」、界面显示「加载中…」。
    expect(triAmount(null, "unknown")).toBe("未判定");
    expect(triAmount(null, "UNKNOWN")).toBe("未判定"); // 大小写不敏感（与 triText 同纪律）
  });

  it("有值但时效存疑 → 数值带标记（IMP-002：stale/degraded 不得与 ready 同形）", () => {
    // 后端五态语义里 stale/degraded **按定义就是「有数据」**（app/core/freshness.py 状态表），
    // 所以「有值」这条分支必须看 state，否则状态标记在界面上被擦掉 = 红线 2 的界面层缺口。
    expect(triAmount(1.2345e11, "stale")).toBe(`${fmtAmount(1.2345e11)}（陈旧）`);
    expect(triAmount(1.2345e11, "degraded")).toBe(`${fmtAmount(1.2345e11)}（降级）`);
    expect(triAmount(1.2345e11, "unknown")).toBe(`${fmtAmount(1.2345e11)}（未判定）`);
    // 三态必须两两可辨 —— 这正是本项要修的东西
    const ready = triAmount(1.2345e11, "ready");
    expect(new Set([ready, triAmount(1.2345e11, "stale"), triAmount(1.2345e11, "degraded")]).size).toBe(3);
  });

  it("有值且状态无异议 → 纯净金额（不添噪）", () => {
    expect(triAmount(1.2345e11, "ready")).toBe(fmtAmount(1.2345e11));
    // unavailable + 有值是自相矛盾的输入：值是真的就照显，但不假装标注
    expect(triAmount(1.2345e11, "unavailable")).toBe(fmtAmount(1.2345e11));
    // 状态未提供 = 无从判定 ⇒ 不加标记（不假装标注），值照显
    expect(triAmount(1.2345e11)).toBe(fmtAmount(1.2345e11));
    expect(triAmount(1.2345e11, "")).toBe(fmtAmount(1.2345e11));
  });

  it("链路正常却无值 → '--'（确为缺失）", () => {
    expect(triAmount(null, "ready")).toBe("--");
    expect(triAmount(null, "stale")).toBe("--");
    expect(triAmount(null, "degraded")).toBe("--");
  });

  it("状态未提供 → '--'（无从判定时不假装在加载）", () => {
    expect(triAmount(null)).toBe("--");
    expect(triAmount(undefined, "")).toBe("--");
  });

  it("NaN 按无值处理（不显示 NaN）", () => {
    expect(triAmount(Number.NaN, "unavailable")).toBe("加载中…");
    expect(triAmount(Number.NaN, "ready")).toBe("--");
  });
});

/** agent 域时间戳 2026-09-12 方案 A 起存**北京 naive**——naive 串必须按 +08:00 显式解释。 */
describe("timeTextBJ / dateTimeTextBJ", () => {
  it("把北京 naive 串原样显示（15:45 就是 15:45，不再 +8h）", () => {
    // 若错误沿用「补 Z 按 UTC 解释」的旧语义，会把已迁移的北京值再偏成次日 23:45
    expect(timeTextBJ("2026-09-11T15:45:06.541226")).toContain("15:45");
    expect(dateTimeTextBJ("2026-09-11T15:45:06.541226")).toContain("09/11");
  });

  it("已带 Z 的串不受影响（按 UTC 解释，不会被二次偏移）", () => {
    // 兼容路径：万一上游给出带 Z 的串（如 JSON 里的历史 UTC 值），仍正确换算
    expect(timeTextBJ("2026-09-11T07:45:06Z")).toContain("15:45");
  });

  it("带偏移的串原样处理", () => {
    expect(timeTextBJ("2026-09-11T15:45:00+08:00")).toContain("15:45");
  });

  it("跨零点正确（带 Z 的 UTC 16:00 → 北京次日 00:00，日期进位）", () => {
    // 注意：zh-CN 的 toLocaleString 用斜杠分隔（09/12），与后端日期串的短横线不同
    expect(dateTimeTextBJ("2026-09-11T16:00:00Z")).toContain("09/12");
  });

  it("空值与非法值返回 --", () => {
    expect(timeTextBJ(null)).toBe("--");
    expect(timeTextBJ("")).toBe("--");
    expect(timeTextBJ("not a date")).toBe("--");
    expect(dateTimeTextBJ(undefined)).toBe("--");
  });

  it("输出不受运行环境时区影响（固定 Asia/Shanghai）", () => {
    // 与 timeText 的差别：timeText 未指定 timeZone，在非 +8 环境会错
    const out = timeTextBJ("2026-09-11T15:45:06");
    expect(out).toContain("15:45");
    expect(out).not.toContain("07:45");
    expect(out).not.toContain("23:45");
  });
});
