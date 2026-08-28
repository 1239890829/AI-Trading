"use client";

import { ReactNode, useEffect, useRef, useState } from "react";

/**
 * 价格 tick 闪烁（工作台的署名动效）：
 * 数值变化时涨闪红 / 跌闪绿 550ms，让每次行情刷新的方向一眼可读。
 * 纯前端感知：prefers-reduced-motion 下自动禁用（见 globals.css）。
 */
export function PriceFlash({ value, children, className }: { value: number | null | undefined; children: ReactNode; className?: string }) {
  const prev = useRef<number | null | undefined>(undefined);
  const [flash, setFlash] = useState<"up" | "down" | "">("");

  useEffect(() => {
    if (value == null) return;
    const p = prev.current;
    prev.current = value;
    if (p == null || p === value) return;
    setFlash(value > p ? "up" : "down");
    const t = setTimeout(() => setFlash(""), 550);
    return () => clearTimeout(t);
  }, [value]);

  return <span className={`${className ?? ""} ${flash === "up" ? "tick-up" : flash === "down" ? "tick-down" : ""}`}>{children}</span>;
}
