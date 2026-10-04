import { describe, expect, it } from "vitest";
import { drawerSnap, drawerTravel, surfaceTransform } from "./surface-motion";

describe("drawer resistance and release intent", () => {
  it("resists travel beyond either anchor without a hard stop", () => {
    expect(drawerTravel(-100, false)).toBeGreaterThan(-100);
    expect(drawerTravel(-200, false)).toBeLessThan(drawerTravel(-100, false));
    expect(drawerTravel(200, true)).toBeLessThan(38);
    expect(drawerTravel(100, false)).toBe(100);
  });
  it("uses the release velocity when a user reverses a long drag", () => {
    expect(drawerSnap(true, -90, .5)).toBe(true);
    expect(drawerSnap(false, 90, -.5)).toBe(false);
    expect(drawerSnap(false, 15, .5)).toBe(true);
    expect(drawerSnap(true, -15, -.5)).toBe(false);
    expect(drawerSnap(true, 12, 0)).toBe(true);
  });
  it("does not create a transform for missing geometry", () => {
    expect(surfaceTransform(new DOMRect(), new DOMRect(0, 0, 600, 400))).toBeNull();
  });
});
