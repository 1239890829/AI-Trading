"use client";

import { useState, type ReactElement } from "react";
import * as Tooltip from "@radix-ui/react-tooltip";

/** Text-only help: never traps focus or becomes a touch-only action. */
export function ControlHint({children, content, inactive = false}: {children: ReactElement; content: string; inactive?: boolean}) {
  const [open, setOpen] = useState(false);
  if (inactive && open) setOpen(false);
  return <Tooltip.Provider delayDuration={350} disableHoverableContent>
    <Tooltip.Root open={open && !inactive} onOpenChange={setOpen}>
      <Tooltip.Trigger asChild>{children}</Tooltip.Trigger>
      <Tooltip.Portal><Tooltip.Content className="control-hint" side="bottom" sideOffset={8} collisionPadding={12}>{content}<Tooltip.Arrow className="control-hint-arrow" width={10} height={5}/></Tooltip.Content></Tooltip.Portal>
    </Tooltip.Root>
  </Tooltip.Provider>;
}
