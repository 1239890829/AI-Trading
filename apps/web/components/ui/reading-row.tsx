"use client";

import type { ButtonHTMLAttributes, HTMLAttributes, MouseEvent } from "react";
import styles from "./reading-row.module.css";

const CONTROL = "button,a,input,select,textarea,summary,[role='button'],[role='link'],[contenteditable='true'],[data-reading-action]";

/** The title remains the keyboard control. Plain space delegates to that same button. */
export function ReadingRow({
  as: Tag = "div",
  className,
  children,
  ...props
}: Omit<HTMLAttributes<HTMLElement>, "onClick" | "onKeyDown"> & { as?: "div" | "li" }) {
  const activate = (event: MouseEvent<HTMLElement>) => {
    // Sibling links retain their default navigation, including modified clicks.
    event.stopPropagation();
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    const target = event.target;
    if (!(target instanceof Element) || target.closest(CONTROL)) return;
    if (window.getSelection()?.isCollapsed === false) return;
    const primary = event.currentTarget.querySelector<HTMLButtonElement>("button[data-reading-primary]");
    if (primary && !primary.disabled) {
      primary.focus({ preventScroll: true });
      primary.click();
    }
  };
  return (
    <Tag
      {...props}
      className={`${styles.row} ${className ?? ""}`}
      onClick={activate}
      onKeyDown={event => {
        if (event.key === "Enter" || event.key === " ") event.stopPropagation();
      }}
    >
      {children}
    </Tag>
  );
}

/** For a row with one action and no nested controls. Native button keyboard behavior stays intact. */
export function ReadingButton({ className, onClick, onKeyDown, ...props }: ButtonHTMLAttributes<HTMLButtonElement>) {
  return (
    <button
      type="button"
      {...props}
      className={`${styles.surface} ${className ?? ""}`}
      onClick={event => { event.stopPropagation(); onClick?.(event); }}
      onKeyDown={event => {
        if (event.key === "Enter" || event.key === " ") event.stopPropagation();
        onKeyDown?.(event);
      }}
    />
  );
}

export const readingRowClasses = { row: styles.row, primary: styles.primary, action: styles.action };
