/** 空仓闸门的「生成时 vs 当前」对照（2026-09-16，猎场头部动态化）。
 *
 *  ## 为什么需要对照
 *
 *  闸门（`picks/gate.evaluate_stand_aside`）原本只在组合生成时算一次并落库定格
 *  全天；而紧挨着它的风格路由是读取时重算的。2026-09-16 实测同一个页面出现
 *  「横幅：建议空仓观望（退潮）」与「chip：题材进攻（高潮）」两个相反结论同屏，
 *  且定格的是更悲观的那个——当日 09:26 生成时在场仅 3 只涨停、最高 2 板，
 *  收盘口径则是涨停 89 家、最高 6 板、1进2 晋级率 36%。
 *
 *  根因不是判据错，是**把开盘 3 分钟的瞬时快照当成了全天结论**。后端因此新增
 *  读取时重算（`meta.gate_live`）。本模块只做一件事：把「两次结论的差异性质」
 *  算成一个可判定的枚举，供 UI 选择展示姿势。
 *
 *  ## 纪律
 *
 *  **本模块不重算闸门规则**——只比较后端已经算好的两个结果。
 *  阈值、相位集合、理由文案一律来自后端（`gate.py` 是唯一真相源）。
 */

import type { StandAsideGate, StandAsideSignals } from "@/lib/api/picks";

/** 两次闸门结论的差异性质。 */
export type GateDrift =
  /** 无差异，或无从比较（缺任一侧）；按原样展示实时结论即可 */
  | "none"
  /** 生成时触发 → 读取时已解除。**今天的主场景**，要点明"已解除"并说明生成时的撤区间不回溯 */
  | "cleared"
  /** 生成时未触发 → 读取时触发。往严的方向变化，必须提示（漏报比误报贵） */
  | "newly_triggered"
  /** 两次都触发，但理由构成变了（相位/指标切换） */
  | "reasons_changed"
  /** 实时复核不可用，展示的是生成时刻结论 */
  | "unavailable";

/** 闸门是否处于"生效"态。**必须显式判 `true`**：
 *  实时复核不可用且旧行无 `gate` 时，对象里没有 `stand_aside` 键，
 *  `undefined` 不能被当作"未触发"（那是把"不知道"伪装成"安全"）。 */
export function gateActive(gate?: StandAsideGate | null): boolean {
  return gate?.stand_aside === true;
}

/** 比较生成时刻结论与读取时刻复核结论。
 *
 *  ⚠️ **只有 `gate_source === "live"` 才做差异判断**。前端把「复核不可用 / 旧后端」
 *  两种形态都退回落库值展示，此时若仍按触发态比较，会出现两种误报：
 *  - 传进来的是 `gate_source="stored"` 的落库对象、`stored` 参数为 undefined
 *    ⇒ 判成 `newly_triggered`，界面写「生成时未触发闸门，按当前盘面复核已触发」——
 *    而事实是生成时就触发了、只是**没复核**（测试实测抓到，2026-09-16）；
 *  - 降级形态（`unavailable`）与落库值逐字相同 ⇒ 判成 `none`，于是"未复核"被静默吞掉。
 *  来源不明确就不做差异判断，是这里唯一正确的姿势。
 */
export function gateDrift(live?: StandAsideGate | null, stored?: StandAsideGate | null): GateDrift {
  if (!live) return "none";
  if (live.gate_source === "unavailable") return "unavailable";
  if (live.gate_source !== "live") return "none";
  const nowOn = gateActive(live);
  const wasOn = gateActive(stored);
  if (nowOn && !wasOn) return "newly_triggered";
  if (!nowOn && wasOn) return "cleared";
  if (nowOn && wasOn) {
    const nowReasons = (live.reasons ?? []).join("|");
    const wasReasons = (stored?.reasons ?? []).join("|");
    return nowReasons === wasReasons ? "none" : "reasons_changed";
  }
  return "none";
}

/** 把闸门输入留痕格式化成可读短句，用于「生成时 vs 当前」逐项对照。
 *
 *  只翻译后端给出的口径，**不做阈值判断**（`< 10 分位线` 这类判语属于 `gate.py`，
 *  已随 `reasons` 带出，此处不复制）。
 */
export function gateInputLines(signals?: StandAsideSignals | null): string[] {
  if (!signals) return [];
  const out: string[] = [];
  const p1 = signals.promotion_1to2;
  if (p1 != null) {
    const pctl = signals.promotion_1to2_pctl;
    out.push(`首板晋级率 ${Math.round(p1 * 100)}%${pctl != null ? `（历史 ${Math.round(pctl)} 分位）` : "（分位不可用）"}`);
  }
  const br = signals.break_rate;
  if (br != null) {
    const pctl = signals.break_rate_pctl;
    // 口径只在**明确标注**时才写：缺字段（旧数据）不猜"真实炸板池"——
    // 两者的可信度不同，猜错等于给用户一个错的置信度。
    const cal =
      signals.break_caliber === "approx" ? "·近似口径"
      : signals.break_caliber === "pool" ? "·真实炸板池"
      : "";
    out.push(`炸板率 ${Math.round(br * 100)}%${pctl != null ? `（历史 ${Math.round(pctl)} 分位）` : ""}${cal}`);
  }
  if (signals.limit_down != null) out.push(`跌停 ${signals.limit_down} 家`);
  const pm = signals.prev_zt_median_pct;
  if (pm != null) out.push(`昨日涨停今日中位 ${pm.toFixed(2)}%`);
  return out;
}

/** 「生成时 → 当前」要展示的对照摘要素材（相位 + 输入），两侧都可能是空的。 */
export interface GateComparison {
  drift: GateDrift;
  /** 生成时刻相位（`stored.phase`） */
  storedPhase: string | null;
  /** 实时相位（`live.recheck.phase`，缺失时退回 `live.phase`） */
  livePhase: string | null;
  storedInputs: string[];
  liveInputs: string[];
}

export function compareGates(live?: StandAsideGate | null, stored?: StandAsideGate | null): GateComparison {
  return {
    drift: gateDrift(live, stored),
    storedPhase: stored?.phase ?? live?.recheck?.stored_phase ?? null,
    livePhase: live?.recheck?.phase ?? live?.phase ?? null,
    storedInputs: gateInputLines(stored?.signals),
    liveInputs: gateInputLines(live?.signals),
  };
}

/** 生成时刻的 HH:MM（北京口径，取自 ISO 字符串的时间部分——不重解析时区）。
 *
 *  后端 `generated_at` 形如 `2026-09-16T09:26:35.371290+08:00`，取 `T` 后两位即可。
 *  无法解析时返回 null，UI 退化为不写时刻（不猜、不显示 "Invalid Date"）。
 */
export function clockOf(iso?: string | null): string | null {
  if (!iso) return null;
  const m = /T(\d{2}):(\d{2})/.exec(iso);
  return m ? `${m[1]}:${m[2]}` : null;
}
