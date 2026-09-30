import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useExitPresence } from "./use-exit-presence";
import { ModalShell } from "@/components/ui/modal-shell";

function Fixture({value, close}: {value: string | null; close: () => void}) {
  const presence = useExitPresence(value);
  return presence.value && <ModalShell open={presence.active} label={presence.value} onClose={close} header={presence.value}><button>正文操作</button></ModalShell>;
}
function media(initial = false) {
  const query = new EventTarget(); let reduced = initial;
  vi.stubGlobal("matchMedia", () => ({get matches() {return reduced;}, addEventListener: query.addEventListener.bind(query), removeEventListener: query.removeEventListener.bind(query)}));
  return (value: boolean) => act(() => {reduced = value; query.dispatchEvent(new Event("change"));});
}
afterEach(() => {cleanup(); vi.useRealTimers(); vi.unstubAllGlobals();});

describe("reading surface exit", () => {
  it("releases focus/inert immediately, hides closed controls, and retains only a bounded visual exit", () => {
    vi.useFakeTimers(); media();
    const origin = document.createElement("button"); document.body.append(origin); origin.focus();
    const close = vi.fn(); const {rerender} = render(<Fixture value="A" close={close} />);
    fireEvent.keyDown(window,{key:"Escape"}); expect(close).toHaveBeenCalledTimes(1);
    rerender(<Fixture value={null} close={close} />);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(document.querySelector('[data-motion-state="closed"]')?.getAttribute("inert")).toBe("");
    expect(origin.inert).not.toBe(true); expect(document.activeElement).toBe(origin);
    fireEvent.keyDown(window,{key:"Escape"}); expect(close).toHaveBeenCalledTimes(1);
    act(() => vi.advanceTimersByTime(160));
    expect(document.querySelector('[data-motion-state="closed"]')).toBeNull(); origin.remove();
  });
  it("reopens in the same DOM before exit finishes without a stale removal or stale content", () => {
    vi.useFakeTimers(); media();
    const {rerender} = render(<Fixture value="A" close={() => {}} />);
    const root = screen.getByRole("presentation");
    rerender(<Fixture value={null} close={() => {}} />);
    act(() => vi.advanceTimersByTime(80));
    rerender(<Fixture value="B" close={() => {}} />);
    expect(screen.getByRole("presentation")).toBe(root);
    expect(screen.getByRole("dialog",{name:"B"})).toBeTruthy();
    act(() => vi.advanceTimersByTime(500));
    expect(screen.getByRole("dialog",{name:"B"})).toBeTruthy();
  });
  it("a live reduced-motion preference change removes the exit immediately and cleans up timers on unmount", () => {
    vi.useFakeTimers(); const reduce = media();
    const {rerender,unmount} = render(<Fixture value="A" close={() => {}} />);
    // React queues its own zero-delay work; only compare timers owned by this surface.
    const rendererTimers = vi.getTimerCount();
    rerender(<Fixture value={null} close={() => {}} />);
    reduce(true);
    expect(document.querySelector('[data-motion-state="closed"]')).toBeNull();
    unmount(); expect(vi.getTimerCount()).toBe(rendererTimers);
  });
  it("reduced motion starts and closes without retained movement or a delayed command", () => {
    vi.useFakeTimers(); media(true);
    const close = vi.fn(); const {rerender} = render(<Fixture value="A" close={close} />);
    fireEvent.click(screen.getByLabelText("关闭")); expect(close).toHaveBeenCalledTimes(1);
    rerender(<Fixture value={null} close={close} />);
    expect(screen.queryByRole("dialog",{hidden:true})).toBeNull();
  });
});

it("closing an ancestor while a nested overlay is open never revives its inert exit or steals focus", () => {
  vi.useFakeTimers(); media();
  const origin = document.createElement("button"); origin.textContent = "页面来源"; document.body.append(origin); origin.focus();
  const lower = render(<Fixture value="父层" close={() => {}} />);
  const upper = render(<ModalShell label="子层" zIndex={60} onClose={() => {}} header="子层">正文</ModalShell>);
  lower.rerender(<Fixture value={null} close={() => {}} />);
  upper.unmount();
  const closed = document.querySelector<HTMLElement>('[data-motion-state="closed"]');
  expect(closed?.inert).toBe(true);
  expect(document.activeElement).toBe(origin);
  act(() => vi.advanceTimersByTime(160)); origin.remove();
});
