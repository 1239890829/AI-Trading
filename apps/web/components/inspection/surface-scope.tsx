"use client";

import { createContext, useContext, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";

type Scope = { search: string; replace: (search: string) => void };
const SurfaceScopeContext = createContext<Scope | null>(null);

/** A reused reader owns its filters locally and cannot rewrite the originating workspace URL. */
export function InspectionScope({ initialSearch, children }: { initialSearch: string; children: React.ReactNode }) {
  const [search, setSearch] = useState(initialSearch);
  return <SurfaceScopeContext.Provider value={{ search, replace: setSearch }}>{children}</SurfaceScopeContext.Provider>;
}

export function useSurfaceScope() {
  const local = useContext(SurfaceScopeContext);
  const route = useSearchParams();
  const search = local?.search ?? route?.toString() ?? "";
  const searchParams = useMemo(() => new URLSearchParams(search), [search]);
  return {
    searchParams,
    embedded: local !== null,
    replaceSearch: (next: URLSearchParams) => {
      if (local) { local.replace(next.toString()); return; }
      const query = next.toString();
      window.history.replaceState({}, "", query ? `?${query}` : window.location.pathname);
    },
  };
}
