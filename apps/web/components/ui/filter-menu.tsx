"use client";

import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { HugeiconsIcon } from "@hugeicons/react";
import ArrowDown01Icon from "@hugeicons/core-free-icons/ArrowDown01Icon";
import Tick02Icon from "@hugeicons/core-free-icons/Tick02Icon";
import { useRef, useState } from "react";

export type FilterOption<T extends string> = {key: T; label: string; title?: string; count?: number};

/** Single-value scope selection. Radix owns keyboard, collision and focus return. */
export function FilterMenu<T extends string>({label, value, options, onChange}: {label: string; value: T; options: readonly FilterOption<T>[]; onChange: (value: T) => void}) {
  const selected = options.find(option => option.key === value);
  const trigger = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  const [container, setContainer] = useState<HTMLElement | undefined>();
  // Keep nested menus inside their dialog's focus/inert boundary.
  return <DropdownMenu.Root open={open} onOpenChange={next => {
    if (next) setContainer(trigger.current?.closest<HTMLElement>('[role="dialog"]') ?? undefined);
    setOpen(next);
  }}>
    <DropdownMenu.Trigger ref={trigger} className="filter-trigger" aria-label={`${label}：${selected?.label ?? "请选择"}`}>
      <span className="filter-label">{label}</span><span className="filter-value" key={value}>{selected?.label ?? "请选择"}</span><HugeiconsIcon icon={ArrowDown01Icon} size={14} strokeWidth={1.6} aria-hidden="true" />
    </DropdownMenu.Trigger>
    <DropdownMenu.Portal container={container}><DropdownMenu.Content className="filter-menu" sideOffset={6} collisionPadding={12} align="start" aria-label={label}>
      <DropdownMenu.Label className="filter-menu-label">{label}</DropdownMenu.Label>
      <DropdownMenu.RadioGroup value={value} onValueChange={key => { const option = options.find(item => item.key === key); if (option) onChange(option.key); }}>
        {options.map(option => <DropdownMenu.RadioItem className="filter-option" key={option.key} value={option.key} textValue={option.label}>
          <span><span className="filter-option-title">{option.label}</span>{option.title && <span className="filter-description">{option.title}</span>}</span>
          {option.count != null && <span className="filter-count">{option.count}</span>}
          <span className="filter-check"><DropdownMenu.ItemIndicator><HugeiconsIcon icon={Tick02Icon} size={15} aria-hidden="true" /></DropdownMenu.ItemIndicator></span>
        </DropdownMenu.RadioItem>)}
      </DropdownMenu.RadioGroup>
    </DropdownMenu.Content></DropdownMenu.Portal>
  </DropdownMenu.Root>;
}
