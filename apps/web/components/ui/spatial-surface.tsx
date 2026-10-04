"use client";

import { useCallback, useEffect, useRef, type HTMLAttributes, type PointerEvent } from "react";
import { useReducedMotion } from "@/hooks/use-exit-presence";

/** A stable hit box around a moving reading plane. Motion never owns layout or focus. */
export function SpatialSurface({ children, className = "", faceClassName = "", ...props }: HTMLAttributes<HTMLDivElement> & { faceClassName?: string }) {
  const gesture = useRef({ x: 0, y: 0, moved: false, down: false });
  const face = useRef<HTMLDivElement>(null);
  const frame = useRef(0);
  const state = useRef({ x: 0, y: 0, vx: 0, vy: 0, tx: 0, ty: 0, last: 0 });
  const reduced = useReducedMotion();

  const stop = useCallback(() => {
    cancelAnimationFrame(frame.current);
    frame.current = 0;
    Object.assign(state.current, { x: 0, y: 0, vx: 0, vy: 0, tx: 0, ty: 0, last: 0 });
    face.current?.style.removeProperty("transform");
    face.current?.removeAttribute("data-spatial-active");
  }, []);

  useEffect(() => {
    stop();
    window.addEventListener("blur", stop);
    window.addEventListener("keydown", stop);
    document.addEventListener("visibilitychange", stop);
    return () => { stop(); window.removeEventListener("blur", stop); window.removeEventListener("keydown", stop); document.removeEventListener("visibilitychange", stop); };
  }, [reduced, stop]);

  function animate(now: number) {
    const s = state.current;
    const step = Math.min((now - (s.last || now - 16)) / 16.67, 2);
    s.last = now;
    const damping = Math.pow(.68, step);
    s.vx = (s.vx + (s.tx - s.x) * .16 * step) * damping;
    s.vy = (s.vy + (s.ty - s.y) * .16 * step) * damping;
    s.x += s.vx * step; s.y += s.vy * step;
    if (face.current) face.current.style.transform = `perspective(1000px) rotateX(${s.x.toFixed(3)}deg) rotateY(${s.y.toFixed(3)}deg)`;
    if (Math.abs(s.tx - s.x) + Math.abs(s.ty - s.y) + Math.abs(s.vx) + Math.abs(s.vy) > .004) frame.current = requestAnimationFrame(animate);
    else { frame.current = 0; s.last = 0; if (s.tx === 0 && s.ty === 0) face.current?.style.removeProperty("transform"); }
  }

  function move(event: PointerEvent<HTMLDivElement>) {
    if (gesture.current.down && Math.hypot(event.clientX - gesture.current.x, event.clientY - gesture.current.y) > 8) gesture.current.moved = true;
    // Touch scrolling keeps a stationary plane.
    if (reduced || !event.isPrimary || event.pointerType === "touch" || event.buttons > 1) return;
    const bounds = event.currentTarget.getBoundingClientRect();
    if (!bounds.width || !bounds.height) return;
    const x = Math.max(-1, Math.min(1, (event.clientX - bounds.left) / bounds.width * 2 - 1));
    const y = Math.max(-1, Math.min(1, (event.clientY - bounds.top) / bounds.height * 2 - 1));
    // At most 5 px of edge displacement, including tall variable-content cards.
    state.current.tx = -y * Math.min(4, Math.atan(10 / bounds.height) * 180 / Math.PI);
    state.current.ty = x * Math.min(4, Math.atan(10 / bounds.width) * 180 / Math.PI);
    face.current?.style.setProperty("--light-x", `${(x + 1) * 50}%`);
    face.current?.style.setProperty("--light-y", `${(y + 1) * 50}%`);
    face.current?.setAttribute("data-spatial-active", "true");
    if (!frame.current) frame.current = requestAnimationFrame(animate);
  }

  function release() {
    state.current.tx = 0; state.current.ty = 0;
    face.current?.removeAttribute("data-spatial-active");
    if (!reduced && !frame.current) frame.current = requestAnimationFrame(animate);
  }

  return <div {...props} className={`spatial-slot ${className}`} onPointerDown={event => { gesture.current = { x: event.clientX, y: event.clientY, moved: false, down: true }; }} onPointerUp={() => { gesture.current.down = false; }} onClickCapture={event => { if (event.detail !== 0 && gesture.current.moved) { event.preventDefault(); event.stopPropagation(); } }} onPointerMove={move} onPointerLeave={release} onPointerCancel={() => { gesture.current.down = false; gesture.current.moved = true; stop(); }} onFocusCapture={stop}>
    <div ref={face} className={`spatial-face ${faceClassName}`}>
      <span className="spatial-reflection" aria-hidden="true" />
      {children}
    </div>
  </div>;
}
