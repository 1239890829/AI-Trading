// @vitest-environment node
import { describe, expect, it } from "vitest";

import { ROLE_FALLBACK_STYLE, ROLE_STYLE, roleClass } from "@/lib/role-style";

/**
 * 角色配色（S2-10）。
 *
 * **键集与后端权威集的全等性由后端测试守卫**（`backend/tests/test_role_style.py`
 * 对 `echelon.ROLE_BASE_SCORE` 逐键比对）——权威只有一处，不在此处再抄一份清单
 * （抄一份就多一处会漂移的地方，正是 S2-10 的病根）。
 *
 * 这里只钉住**运行期语义**：任何输入都必须拿到一个可用的 class 字符串，
 * 尤其是未知角色不得退化为 `undefined`（无样式徽标在深色底上近乎不可见）。
 */
describe("roleClass", () => {
  it("已知角色取表内样式", () => {
    expect(roleClass("龙头")).toBe(ROLE_STYLE["龙头"]);
    expect(roleClass("空间板")).toBe(ROLE_STYLE["空间板"]);
    expect(roleClass("断板")).toBe(ROLE_STYLE["断板"]);
  });

  it("未知角色退化为中性兜底，而不是 undefined", () => {
    // 历史缺陷形态：题材看板缺 领涨/滞涨/同步 三键 → ROLE_STYLE[role] === undefined
    const out = roleClass("未来才有的新角色");
    expect(out).toBe(ROLE_FALLBACK_STYLE);
    expect(typeof out).toBe("string");
    expect(out.length).toBeGreaterThan(0);
  });

  it("空值一律兜底", () => {
    expect(roleClass(null)).toBe(ROLE_FALLBACK_STYLE);
    expect(roleClass(undefined)).toBe(ROLE_FALLBACK_STYLE);
    expect(roleClass("")).toBe(ROLE_FALLBACK_STYLE);
  });

  it("表内每个角色都有非空样式（防手滑写成空串）", () => {
    for (const [role, cls] of Object.entries(ROLE_STYLE)) {
      expect(cls, `角色 ${role} 的样式为空`).toBeTruthy();
      expect(cls.trim().length).toBeGreaterThan(0);
    }
  });

  it("领涨/滞涨/同步 必须在表内（S2-10 修复的三个缺失键）", () => {
    for (const role of ["领涨", "滞涨", "同步"]) {
      expect(ROLE_STYLE[role], `缺 ${role}`).toBeTruthy();
      expect(roleClass(role)).not.toBe(ROLE_FALLBACK_STYLE);
    }
  });

  it("僵尸键 情绪票 已移除（后端无任何产出方）", () => {
    expect(ROLE_STYLE["情绪票"]).toBeUndefined();
  });
});
