import { readdirSync, readFileSync, statSync } from "node:fs";
import path from "node:path";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

import { QualityBadge } from "@/components/quality-badge";
import { lineOf, maskComments } from "@/lib/src-mask";

// vitest 未开 globals 时 RTL 自动 cleanup 不注册，必须手动（见 trade-form.test.tsx 注释）
afterEach(cleanup);

/**
 * 质量徽标：**语义** + **防漂移守卫**（2026-09-14 用户报障「正常标签消失」）。
 *
 * ## 真实缺陷（本轮）
 * 同一「指数质量」概念、同一份数据，在**盘面页**显示「正常」、在**工作台**不显示 ——
 * 因为 5 个调用点各自判断：`market/page.tsx` 无条件渲染，另 3 处用 `isHardQuality`
 * 门控。而 `isHardQuality` 的 docstring 写明它只回答「是否**硬**质量问题」，
 * 09-02 的降噪目标**只有 low/medium 两档**（盘中瞬态闪烁），`high`（正常）
 * **从未被决策过** —— 把它一起隐掉是**副作用的误用**，不是设计。
 *
 * ## 修法
 * 可见性策略收敛到**唯一决策点** `lib/format.ts::shouldShowQualityBadge`，
 * 并**由组件自己门控**（`QualityBadge` 内部早退）。调用点一律直接渲染，
 * 不得再写 `{isHardQuality(...) && <QualityBadge/>}`——那正是漂移成因。
 *
 * ## 本守卫的判定面与边界（诚实标注）
 * 判据 = 「策略性判断**只能**出现在策略单点（`lib/format.ts`）与执行者
 * （`components/quality-badge.tsx`）两处」。它**抓不到**在调用点手写等价的
 * 档位列表（如 `q !== "low" && q !== "medium"` 内联）——那种写法在大面积
 * 误报风险下无法廉价区分（全仓有大量与「质量」无关的 low/medium 语义，
 * 如情绪温度、角色样式）。覆盖面与判据同等重要：**本守卫全绿 ≠ 口径不会漂移**，
 * 它只挡住**已被真实踩中过的那条**路径。
 */

describe("QualityBadge 可见性策略（唯一决策点）", () => {
  it("high（正常）渲染为静音灰「正常」——不得静默消失，否则与「字段缺失」不可辨", () => {
    render(<QualityBadge quality="high" />);
    expect(screen.getByText("正常")).toBeTruthy();
  });

  it("low / medium（盘中瞬态）不渲染——渲染就闪（2026-09-02 修复对象）", () => {
    const low = render(<QualityBadge quality="low" reasons={["change_pct_mismatch"]} />);
    expect(low.container.textContent).toBe("");
    low.unmount();
    const medium = render(<QualityBadge quality="medium" />);
    expect(medium.container.textContent).toBe("");
  });

  it("stale/invalid 常显；stale 带 market_closed 时是「休市」而非「过期」（常态 vs 故障）", () => {
    const stale = render(<QualityBadge quality="stale" reasons={["refresh_failed"]} />);
    expect(stale.container.textContent).toBe("过期");
    stale.unmount();
    const closed = render(<QualityBadge quality="stale" reasons={["market_closed"]} />);
    expect(closed.container.textContent).toBe("休市");
    closed.unmount();
    const invalid = render(<QualityBadge quality="invalid" reasons={["invalid_symbol"]} />);
    expect(invalid.container.textContent).toBe("非法");
  });

  it("源时间被拒绝时 stale 徽标把降级原因翻译成人话", () => {
    render(<QualityBadge quality="stale" reasons={["source_time_regress_ignored"]} />);
    const badge = screen.getByText("过期");
    expect(badge.getAttribute("title")).toContain("源返回晚到旧行情");
  });

  it("reasons 同时含 market_closed 与其它原因时仍判「休市」（休市优先，不降级为故障红）", () => {
    const { container } = render(
      <QualityBadge quality="stale" reasons={["refresh_failed", "market_closed"]} />,
    );
    expect(container.textContent).toBe("休市");
  });
});

// ---------------------------------------------------------------- 防漂移守卫

const WEB_ROOT = path.resolve(import.meta.dirname, "..");
const SKIP_DIRS = new Set(["node_modules", ".next", "out", "dist", "coverage", ".turbo"]);

/** 策略单点与其执行者：这两个文件**允许**出现策略性判断。 */
const POLICY_FILES = new Set([
  path.join("lib", "format.ts"),
  path.join("components", "quality-badge.tsx"),
]);

/** 只许出现在策略单点/执行者的标识符。 */
const POLICY_SYMBOLS = ["isHardQuality(", "shouldShowQualityBadge("];

function walk(dir: string, out: string[] = []): string[] {
  for (const entry of readdirSync(dir)) {
    if (SKIP_DIRS.has(entry)) continue;
    const full = path.join(dir, entry);
    if (statSync(full).isDirectory()) walk(full, out);
    else if (/\.(ts|tsx)$/.test(entry)) out.push(full);
  }
  return out;
}

describe("防漂移守卫：徽标可见性策略不得在调用点各自判断", () => {
  it("策略性判断只出现在 lib/format.ts 与 components/quality-badge.tsx", () => {
    const offenders: string[] = [];
    for (const full of walk(WEB_ROOT)) {
      const rel = path.relative(WEB_ROOT, full);
      if (/\.(test|spec)\.(m|c)?[jt]sx?$/.test(rel)) continue; // 测试要驱动该判据
      if (POLICY_FILES.has(rel)) continue;
      // 掩注释：修复代码的注释必然要**提到**被判定的标识符，
      // 不掩掉就会假红，而假红会引导后人删掉正确的说明（KB-ENG-66）。
      const masked = maskComments(readFileSync(full, "utf8"));
      for (const sym of POLICY_SYMBOLS) {
        const at = masked.indexOf(sym);
        if (at >= 0) offenders.push(`${rel}:${lineOf(masked, at)} 出现 ${sym}`);
      }
    }
    expect(offenders).toEqual([]);
  });

  it("调用点一律直接渲染 QualityBadge（无 `{… && <QualityBadge>}` 这类门控）", () => {
    const gated: string[] = [];
    for (const full of walk(WEB_ROOT)) {
      const rel = path.relative(WEB_ROOT, full);
      if (/\.(test|spec)\.(m|c)?[jt]sx?$/.test(rel)) continue;
      const masked = maskComments(readFileSync(full, "utf8"));
      let at = masked.indexOf("<QualityBadge");
      while (at >= 0) {
        // 只回看**同一表达式内**的最近 2 个非空白字符：`&&` 紧贴标签即视为门控。
        const before = masked.slice(Math.max(0, at - 24), at).replace(/\s+/g, "");
        if (before.endsWith("&&")) gated.push(`${rel}:${lineOf(masked, at)}`);
        at = masked.indexOf("<QualityBadge", at + 1);
      }
    }
    expect(gated).toEqual([]);
  });
});
