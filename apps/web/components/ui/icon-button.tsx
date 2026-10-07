"use client";

import { HugeiconsIcon } from "@hugeicons/react";
import Cancel01Icon from "@hugeicons/core-free-icons/Cancel01Icon";
import type { ButtonHTMLAttributes, ReactNode } from "react";

/** One optical face; a separate 44px coarse-pointer hit target. */
export function IconButton({className = "", children, ...props}: ButtonHTMLAttributes<HTMLButtonElement> & {children: ReactNode; "aria-label": string}) {
  return <button type="button" {...props} className={`icon-button ${className}`}><span className="icon-button-face">{children}</span></button>;
}

export function CloseButton({onClose, label = "关闭", autoFocus = true}: {onClose: () => void; label?: string; autoFocus?: boolean}) {
  return <IconButton onClick={onClose} aria-label={label} title={`${label}（Esc）`} data-overlay-autofocus={autoFocus || undefined} className="ui-close"><HugeiconsIcon icon={Cancel01Icon} size={16} strokeWidth={1.7} aria-hidden="true" /></IconButton>;
}
