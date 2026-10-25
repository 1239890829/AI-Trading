import type {HotTheme} from "@/lib/api/themes";

export interface HotThemeGroup {primary: HotTheme; related: HotTheme[]}

/** Presentation only: shared hot-stock samples do not establish an official taxonomy. */
export function groupHotThemes(rows: readonly HotTheme[], compact = true): HotThemeGroup[] {
  const groups: HotThemeGroup[] = [];
  for (const row of rows) {
    const members = new Set(row.hot_symbols);
    const group = compact && members.size >= 3 ? groups.find(({primary}) => {
      const anchor = new Set(primary.hot_symbols);
      if (anchor.size < 3) return false;
      const common = [...members].filter(symbol => anchor.has(symbol)).length;
      return common / (members.size + anchor.size - common) >= .75;
    }) : undefined;
    if (group) group.related.push(row);
    else groups.push({primary: row, related: []});
  }
  return groups;
}
