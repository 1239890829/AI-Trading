import {useEffect, useState} from "react";
import {act, cleanup, fireEvent, render, screen} from "@testing-library/react";
import {afterEach, beforeEach, describe, expect, it, vi} from "vitest";
import {ModalShell} from "./modal-shell";
import {useExitPresence} from "@/hooks/use-exit-presence";
import type {MotionOrigin} from "@/lib/surface-motion";

const stopped = vi.fn(), operated = vi.fn(), polled = vi.fn();
const animations: Array<{cancel: ReturnType<typeof vi.fn>; playState: string}> = [];
const animate = vi.fn((_frames: Keyframe[], _options: KeyframeAnimationOptions) => {
  const animation = {cancel:vi.fn(), playState:"running"}; animations.push(animation); return animation as unknown as Animation;
});
let reduce = false;
beforeEach(() => {
  vi.useFakeTimers(); reduce = false; stopped.mockClear(); operated.mockClear(); polled.mockClear(); animate.mockClear(); animations.length = 0;
  vi.stubGlobal("matchMedia", () => ({matches:reduce, addEventListener:vi.fn(), removeEventListener:vi.fn()}));
  vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function(this: HTMLElement) {
    // Deliberately transformed: freezing this rect would incorrectly shrink the exit.
    return this.getAttribute("role") === "dialog" ? new DOMRect(10, 10, 300, 200) : new DOMRect(24, 70, 280, 160);
  });
  vi.spyOn(HTMLElement.prototype, "offsetWidth", "get").mockImplementation(function(this: HTMLElement) {return this.getAttribute("role") === "dialog" ? 600 : 560;});
  vi.spyOn(HTMLElement.prototype, "offsetHeight", "get").mockImplementation(function(this: HTMLElement) {return this.getAttribute("role") === "dialog" ? 400 : 320;});
  vi.spyOn(HTMLElement.prototype, "offsetLeft", "get").mockReturnValue(14);
  vi.spyOn(HTMLElement.prototype, "offsetTop", "get").mockReturnValue(60);
  Object.defineProperty(HTMLElement.prototype, "animate", {value:animate, configurable:true});
});
afterEach(() => { cleanup(); vi.useRealTimers(); vi.restoreAllMocks(); vi.unstubAllGlobals(); delete (HTMLElement.prototype as Partial<HTMLElement>).animate; });

function Reader({symbol, canvas = false, media = false}: {symbol: string; canvas?: boolean; media?: boolean}) {
  useEffect(() => {
    const timer = setInterval(() => polled(symbol), 20);
    return () => { clearInterval(timer); stopped(symbol); };
  }, [symbol]);
  return <><label htmlFor="draft">草稿<input id="draft" name="draft" aria-label="草稿" defaultValue={symbol} /></label><button id="operation" onClick={() => operated(symbol)}>操作{symbol}</button><a href="https://example.com" target="_blank">原文链接</a>{canvas && <canvas />}{media && <><img src="data:image/png;base64," alt="封面" /><iframe src="about:blank" title="外部内容" /></>}</>;
}
function Host({origin, canvas = false, media = false}: {origin?: MotionOrigin | null; canvas?: boolean; media?: boolean}) {
  const [symbol, setSymbol] = useState<string | null>(null);
  const presence = useExitPresence(symbol);
  return <><button onClick={() => setSymbol("600825")}>打开甲</button><button onClick={() => setSymbol("600127")}>打开乙</button>{presence.value && <ModalShell label={`详情${presence.value}`} testid="exit-shell" open={presence.active} motionOrigin={origin} onClose={() => setSymbol(null)} header="详情"><Reader symbol={presence.value} canvas={canvas} media={media} /></ModalShell>}</>;
}

describe("closed reading surfaces", () => {
  it("keeps only a sanitized DOM image while the real reader and its effects stop immediately", () => {
    render(<Host origin={null} />);
    const trigger = screen.getByRole("button", {name:"打开甲"}); trigger.focus(); fireEvent.click(trigger);
    fireEvent.change(screen.getByRole("textbox", {name:"草稿"}), {target:{value:"未提交"}});
    fireEvent.click(screen.getByRole("button", {name:"关闭"}));
    expect(stopped).toHaveBeenCalledWith("600825");
    expect(document.activeElement).toBe(trigger);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.queryByRole("button", {name:"操作600825"})).toBeNull();
    const shell = screen.getByTestId("exit-shell"), image = shell.querySelector<HTMLElement>("[data-exit-visual]")!;
    expect(image).toBeTruthy(); expect(image.inert).toBe(true); expect(image.getAttribute("aria-hidden")).toBe("true");
    expect(image.querySelector("[id], [name], [for], [href], [target], [data-testid]")).toBeNull();
    expect(image.querySelector<HTMLInputElement>("input")!.value).toBe("未提交");
    fireEvent.click(image.querySelector("button")!); expect(operated).not.toHaveBeenCalled();
    expect(shell.style.height).toContain("min(400px"); expect(shell.style.width).toContain("100vw");
    // A resize cannot enlarge the captured panel beyond the current viewport clamp.
    fireEvent(window, new Event("resize")); expect(shell.style.height).toContain("100dvh");
    expect(shell.closest("[data-motion-state]")?.getAttribute("data-motion-state")).toBe("closed");
    expect(shell.getAttribute("aria-modal")).toBeNull();
    act(() => { vi.advanceTimersByTime(160); }); expect(screen.queryByTestId("exit-shell")).toBeNull(); expect(polled).not.toHaveBeenCalled();
  });

  it("quick reopen removes the old image, restores layout and cannot revive old commands", () => {
    render(<Host origin={null} />);
    fireEvent.click(screen.getByRole("button", {name:"打开甲"}));
    fireEvent.click(screen.getByRole("button", {name:"关闭"}));
    expect(document.querySelector("[data-exit-visual]")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", {name:"打开乙"}));
    expect(document.querySelector("[data-exit-visual]")).toBeNull();
    expect(screen.queryByText("操作600825")).toBeNull();
    fireEvent.click(screen.getByRole("button", {name:"操作600127"})); expect(operated).toHaveBeenCalledWith("600127");
    expect(screen.getByRole("dialog").style.height).toBe("");
    expect(animations[1].cancel).toHaveBeenCalled();
    act(() => { vi.advanceTimersByTime(160); }); expect(screen.getByRole("dialog", {name:"详情600127"})).toBeTruthy();
  });

  it.each(["disconnected", "zero", "offscreen"])("a %s source still enters and closes through a fallback", kind => {
    const element = document.createElement("button");
    if (kind !== "disconnected") document.body.append(element);
    vi.spyOn(element, "getBoundingClientRect").mockReturnValue(kind === "zero" ? new DOMRect(0, 0, 0, 0) : new DOMRect(-900, -900, 20, 20));
    const view = render(<Host origin={{element, kind:"capsule"}} />);
    fireEvent.click(screen.getByRole("button", {name:"打开甲"}));
    expect(animate).toHaveBeenCalledTimes(1);
    expect(animate.mock.calls[0][0][0].opacity).toBe(0);
    fireEvent.click(screen.getByRole("button", {name:"关闭"}));
    expect(animate).toHaveBeenCalledTimes(2);
    expect(animate.mock.calls[1][0].at(-1)?.opacity).toBe(0);
    view.unmount(); element.remove(); expect(document.querySelector("[data-exit-visual]")).toBeNull();
  });

  it("without WAAPI the CSS owner remains available, including keyboard-origin entry", () => {
    delete (HTMLElement.prototype as Partial<HTMLElement>).animate;
    render(<Host origin={null} />); fireEvent.click(screen.getByRole("button", {name:"打开甲"}));
    const shell = screen.getByRole("dialog");
    expect(shell.dataset.motionOwner).toBeUndefined(); expect(shell.dataset.motionImmediate).toBeUndefined();
    fireEvent.click(screen.getByRole("button", {name:"关闭"}));
    expect(shell.closest("[data-motion-state]")?.getAttribute("data-motion-state")).toBe("closed");
  });

  it("failed Canvas copying does not delay business close or retain a reader", () => {
    vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({drawImage() {throw new Error("unavailable bitmap");}} as unknown as CanvasRenderingContext2D);
    render(<Host origin={null} canvas />); fireEvent.click(screen.getByRole("button", {name:"打开甲"}));
    fireEvent.click(screen.getByRole("button", {name:"关闭"}));
    expect(stopped).toHaveBeenCalledWith("600825"); expect(screen.queryByRole("dialog")).toBeNull();
    act(() => { vi.advanceTimersByTime(160); }); expect(document.querySelector("[data-exit-visual]")).toBeNull();
  });

  it("filters active media before copying, without constructing a cloned src-bearing element", () => {
    render(<Host origin={null} media />); fireEvent.click(screen.getByRole("button", {name:"打开甲"}));
    const deepCopy = vi.spyOn(Node.prototype, "cloneNode"), create = vi.spyOn(document, "createElementNS");
    fireEvent.click(screen.getByRole("button", {name:"关闭"}));
    expect(deepCopy).not.toHaveBeenCalled();
    expect(create.mock.calls.some(call => ["img", "iframe"].includes(call[1]))).toBe(false);
    expect(document.querySelector("[data-exit-visual] img, [data-exit-visual] iframe, [data-exit-visual] [src], [data-exit-visual] [srcset]")).toBeNull();
    expect(document.querySelector("[data-exit-visual] canvas")).toBeTruthy();
  });

  it("reduced motion goes directly to the terminal state with no visual image", () => {
    reduce = true; render(<Host origin={null} />); fireEvent.click(screen.getByRole("button", {name:"打开甲"}));
    fireEvent.click(screen.getByRole("button", {name:"关闭"}));
    expect(animate).not.toHaveBeenCalled(); expect(stopped).toHaveBeenCalledWith("600825");
    expect(screen.queryByTestId("exit-shell")).toBeNull(); expect(document.querySelector("[data-exit-visual]")).toBeNull();
  });
});
