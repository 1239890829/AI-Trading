import { describe, expect, it } from "vitest";

import type { StandAsideGate } from "@/lib/api";
import { clockOf, compareGates, gateActive, gateDrift, gateInputLines } from "@/lib/picks-gate";

/** 生成时刻落库值：09:26 的快照——当日实测在场仅 3 只涨停、最高 2 板 ⇒ 判「退潮」。 */
const stored: StandAsideGate = {
  stand_aside: true,
  level: "strong",
  reasons: ["情绪相位「退潮」——赚钱效应处于周期低位", "首板晋级率 8% 处于历史 9 分位"],
  advice: "市场情绪明显转弱，建议空仓观望，切忌盲目出手",
  phase: "退潮",
  strip_buy_range: true,
  gate_source: "stored",
  signals: {
    promotion_1to2: 0.08,
    promotion_1to2_pctl: 9,
    break_rate: null,
    break_rate_pctl: null,
    limit_down: 0,
    prev_zt_median_pct: 0,
    promo_caliber: "percentile",
    break_caliber: "missing",
  },
};

/** 读取时刻复核：收盘口径 ⇒ 相位「高潮」，闸门自然不该触发。 */
const liveCleared: StandAsideGate = {
  stand_aside: false,
  level: "none",
  reasons: [],
  advice: "市场情绪未见系统性风险，按组合纪律执行即可",
  phase: "高潮",
  strip_buy_range: false,
  gate_source: "live",
  signals: {
    promotion_1to2: 0.36,
    promotion_1to2_pctl: 62,
    break_rate: 0.11,
    break_rate_pctl: 30,
    limit_down: 0,
    prev_zt_median_pct: 1.06,
    promo_caliber: "percentile",
    break_caliber: "pool",
  },
  recheck: {
    phase: "高潮",
    trade_date: "2026-09-16",
    judged_at: "2026-09-16T10:48:06+00:00",
    stored_phase: "退潮",
    phase_changed: true,
    inputs_source: "sentiment.gate_inputs",
    break_caliber: "pool",
  },
};

describe("gateActive：只认显式 true", () => {
  it("undefined / 空对象 / 缺键都不算触发（不把「不知道」伪装成「安全」）", () => {
    expect(gateActive(undefined)).toBe(false);
    expect(gateActive(null)).toBe(false);
    expect(gateActive({} as StandAsideGate)).toBe(false);
    // 实时复核不可用且旧行无 gate 时的真实形状：连 stand_aside 键都没有
    expect(gateActive({ gate_source: "unavailable", gate_note: "快照未就绪" } as StandAsideGate)).toBe(false);
  });

  it("显式 true / false 各归其位", () => {
    expect(gateActive(stored)).toBe(true);
    expect(gateActive(liveCleared)).toBe(false);
  });
});

describe("gateDrift：生成时 vs 当前的差异性质", () => {
  it("cleared：生成时触发，现在已解除（2026-09-16 主场景）", () => {
    expect(gateDrift(liveCleared, stored)).toBe("cleared");
  });

  it("newly_triggered：生成时没触发，盘中变严（漏报比误报贵，方向不能反）", () => {
    const liveOn: StandAsideGate = { ...stored, gate_source: "live" };
    const storedOff: StandAsideGate = { ...stored, stand_aside: false, reasons: [], phase: "高潮", gate_source: "stored" };
    expect(gateDrift(liveOn, storedOff)).toBe("newly_triggered");
  });

  it("reasons_changed：两次都触发但理由构成变了", () => {
    const liveOn: StandAsideGate = {
      ...stored,
      gate_source: "live",
      reasons: ["炸板率 41% 处于历史 95 分位——封板异常不牢"],
    };
    expect(gateDrift(liveOn, stored)).toBe("reasons_changed");
  });

  it("none：两次都触发且理由逐字相同", () => {
    const liveOn: StandAsideGate = { ...stored, gate_source: "live", recheck: liveCleared.recheck };
    expect(gateDrift(liveOn, stored)).toBe("none");
  });

  it("unavailable 优先于触发态比较——降级面不能被读成「已触发」或「已解除」", () => {
    // 降级时 live 对象其实就是落库内容（stand_aside=true），若按触发态比较会判成 none，
    // 于是界面既不提示"已解除"也不提示"未复核"；必须显式归为 unavailable。
    const degraded: StandAsideGate = { ...stored, gate_source: "unavailable", gate_note: "实时情绪不可用（全市场快照尚未就绪）" };
    expect(gateDrift(degraded, stored)).toBe("unavailable");
  });

  it("缺任一侧 → none（无从比较，不臆造差异）", () => {
    expect(gateDrift(undefined, stored)).toBe("none");
    expect(gateDrift(liveCleared, undefined)).toBe("none");
    // 旧后端不返回 gate_live 时，界面上只有落库值：它不是"已解除"
    expect(gateDrift(stored, undefined)).toBe("none");
  });
});

describe("gateInputLines：输入留痕翻译（不做阈值判断）", () => {
  it("晋级率带分位、炸板率带口径、跌停与昨日中位照实写", () => {
    expect(gateInputLines(liveCleared.signals)).toEqual([
      "首板晋级率 36%（历史 62 分位）",
      "炸板率 11%（历史 30 分位）·真实炸板池",
      "跌停 0 家",
      "昨日涨停今日中位 1.06%",
    ]);
  });

  it("分位不可用时写明「分位不可用」，不回落到绝对阈值判语（判语属后端）", () => {
    const lines = gateInputLines({ promotion_1to2: 0.08, promotion_1to2_pctl: null });
    expect(lines).toEqual(["首板晋级率 8%（分位不可用）"]);
  });

  it("炸板率口径缺字段时不猜（避免给出错的置信度）", () => {
    const lines = gateInputLines({ break_rate: 0.11 });
    expect(lines).toEqual(["炸板率 11%"]);
    expect(lines[0]).not.toContain("真实炸板池");
  });

  it("近似口径明确标注（真实炸板池 vs 价格法近似可信度不同）", () => {
    expect(gateInputLines({ break_rate: 0.3, break_caliber: "approx" })[0]).toContain("近似口径");
  });

  it("无 signals / 空对象 → 空数组（整节不渲染）", () => {
    expect(gateInputLines(undefined)).toEqual([]);
    expect(gateInputLines({})).toEqual([]);
  });
});

describe("compareGates：对照素材组装", () => {
  it("相位两侧分别取 recheck.phase 与 stored.phase", () => {
    const cmp = compareGates(liveCleared, stored);
    expect(cmp.drift).toBe("cleared");
    expect(cmp.storedPhase).toBe("退潮");
    expect(cmp.livePhase).toBe("高潮");
    expect(cmp.storedInputs[0]).toContain("8%");
    expect(cmp.liveInputs[0]).toContain("36%");
  });

  it("无 recheck（未降级的旧形态）时实时相位退回 live.phase", () => {
    const bare: StandAsideGate = { ...liveCleared, recheck: null };
    expect(compareGates(bare, stored).livePhase).toBe("高潮");
  });

  it("stored 缺失时相位退回 recheck.stored_phase（不显示「未知」）", () => {
    const cmp = compareGates(liveCleared, undefined);
    expect(cmp.storedPhase).toBe("退潮");
  });
});

describe("clockOf：生成时刻提取", () => {
  it("从带偏移的 ISO 取 HH:MM（不重解析时区，避免 UTC 跑测试时整体偏移）", () => {
    expect(clockOf("2026-09-16T09:26:35.371290+08:00")).toBe("09:26");
    expect(clockOf("2026-09-16T01:26:35Z")).toBe("01:26");
  });

  it("无法解析 → null（界面退化为不写时刻，而非 Invalid Date）", () => {
    expect(clockOf(undefined)).toBeNull();
    expect(clockOf(null)).toBeNull();
    expect(clockOf("")).toBeNull();
    expect(clockOf("2026-09-16")).toBeNull();
  });
});
