import { describe, expect, it, vi } from "vitest";
import { inspectionClick, inspectionRequestForHref } from "./inspection-context";

describe("contextual inspection target contract", () => {
  it("preserves the original message date and version and rejects partially bound reviews", () => {
    const version = "a".repeat(64);
    expect(inspectionRequestForHref(`/hunting?view=review&date=2026-10-02&version=${version}`)).toEqual({kind: "selection-review", date: "2026-10-02", version});
    expect(inspectionRequestForHref(`/hunting?view=review&version=${version}`)).toBeNull();
    expect(inspectionRequestForHref("/hunting?view=review&date=2026-10-02")).toBeNull();
    expect(inspectionRequestForHref("/hunting?view=review&date=2026-10-02&version=unknown")).toBeNull();
    expect(inspectionRequestForHref("/hunting?review=1&tag=pick")).toEqual({kind: "selection-preview", view: "review", section: "daily", theme: undefined, date: undefined});
    expect(inspectionRequestForHref("/hunting?view=evidence&date=2026-10-02")).toEqual({kind: "selection-preview", view: "evidence", section: undefined, theme: undefined, date: "2026-10-02"});
  });

  it("carries pool membership and date without taking over external or unknown page navigation", () => {
    expect(inspectionRequestForHref("/tape?tab=limitup&date=20261002&theme=%E7%B2%AE%E9%A3%9F&symbols=600127,002636"))
      .toEqual({kind: "limit-up", date: "20261002", theme: "粮食", symbols: ["600127", "002636"]});
    for (const href of ["https://example.com/tape?tab=limitup", "//example.com/tape?tab=limitup", "/market?tab=overview", "/market?tab=heatmap&date=20261002", "/agent?tab=tasks", "/workbench?symbol=600127", "/tape?tab=unknown", "/tape?tab=limitup&date=not-a-day"]) {
      expect(inspectionRequestForHref(href)).toBeNull();
    }
  });

  it("keeps account observations separate from security detail targets", () => {
    expect(inspectionRequestForHref("/tape?tab=longhu&date=20261002")).toEqual({kind: "longhu", date: "20261002"});
    expect(inspectionRequestForHref("/market?tab=heatmap")).toEqual({kind: "heatmap"});
    expect(inspectionRequestForHref("/workbench?mode=positions&account=daily")).toEqual({kind: "account", scope: "daily"});
    expect(inspectionRequestForHref("/workbench?mode=positions&account=paper")).toEqual({kind: "account", scope: "main"});
    expect(inspectionRequestForHref("/workbench?mode=positions")).toEqual({kind: "account", scope: "manual"});
    expect(inspectionRequestForHref("/workbench?mode=positions&account=unknown")).toBeNull();
    expect(inspectionRequestForHref("/workbench?mode=positions&symbol=600127")).toBeNull();
  });

  it("rejects cross-origin browser normalization and impossible calendar dates", () => {
    for (const href of ["/\\evil.example/tape?tab=limitup", "/\\evil.example/hunting?sec=daily", "/\\inspection.invalid/tape?tab=limitup", "/\n/inspection.invalid/tape?tab=limitup", "/tape?tab=limitdown&date=2026-13-32", "/tape?tab=longhu&date=20260229", "/hunting?view=evidence&date=1900-02-29", "/tape?tab=themes&date=00000101"]) {
      expect(inspectionRequestForHref(href)).toBeNull();
    }
    expect(inspectionRequestForHref("/tape?tab=limitdown&date=20240229")).toEqual({kind: "limit-down", date: "20240229"});
    expect(inspectionRequestForHref("/tape?tab=themes&date=2000-02-29")).toEqual({kind: "themes", date: "2000-02-29", focus: undefined});
  });

  it("does not widen malformed pool membership or discard explicit theme filters", () => {
    for (const href of ["/tape?tab=limitup&symbols=600127,bad", "/tape?tab=limitup&symbols=bad", "/tape?tab=limitup&symbols=", "/tape?tab=themes&sort=count", "/tape?tab=themes&min_boards=2", "/tape?tab=themes&min_count=5", "/tape?tab=themes&broken=unknown"]) {
      expect(inspectionRequestForHref(href)).toBeNull();
    }
    expect(inspectionRequestForHref("/tape?tab=themes&date=20261002&focus=%E7%B2%AE%E9%A3%9F&broken=1"))
      .toEqual({kind: "themes", date: "20261002", focus: "粮食", brokenOnly: true});
    expect(inspectionRequestForHref("/tape?tab=limitup&symbols=600127,002636"))
      .toEqual({kind: "limit-up", date: undefined, theme: undefined, symbols: ["600127", "002636"]});
  });

  it("keeps supported event filters and declines unknown or unsupported historical scope", () => {
    expect(inspectionRequestForHref("/market?tab=events&four=policy&tag=%E5%85%AC%E5%91%8A&sort=impact&l1=1&target=600127"))
      .toEqual({kind: "events", target: "600127", filter: {four: "policy", tag: "公告", sort: "impact", l1Only: true}});
    expect(inspectionRequestForHref("/hunting?sec=opportunity"))
      .toEqual({kind: "selection-preview", view: "discover", section: "opportunity", theme: undefined, date: undefined});
    for (const href of ["/market?tab=fund&date=20261002", "/market?tab=events&date=20261002", "/market?tab=events&four=unknown", "/market?tab=events&tag=unknown", "/market?tab=events&sort=unknown", "/market?tab=events&l1=yes", "/hunting?sec=unknown"]) {
      expect(inspectionRequestForHref(href)).toBeNull();
    }
  });

  it("intercepts ordinary clicks once while leaving modified or handled clicks to the browser", () => {
    const open = vi.fn();
    const click = inspectionClick(open, {kind: "fund"});
    const makeEvent = (patch: Record<string, unknown> = {}) => ({button: 0, defaultPrevented: false, metaKey: false, ctrlKey: false, shiftKey: false, altKey: false, detail: 0, preventDefault: vi.fn(), stopPropagation: vi.fn(), ...patch}) as unknown as React.MouseEvent<HTMLElement>;
    const plain = makeEvent();
    click(plain);
    expect(open).toHaveBeenCalledOnce();
    expect(plain.preventDefault).toHaveBeenCalledOnce();
    expect(plain.stopPropagation).toHaveBeenCalledOnce();
    for (const patch of [{ctrlKey: true}, {metaKey: true}, {shiftKey: true}, {altKey: true}, {button: 1}, {defaultPrevented: true}]) {
      const event = makeEvent(patch);
      click(event);
      expect(event.preventDefault).not.toHaveBeenCalled();
    }
    expect(open).toHaveBeenCalledOnce();
  });
});
