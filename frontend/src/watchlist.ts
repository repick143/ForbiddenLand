import type { MarketAsset } from "./types";

export interface WatchlistGroup {
  id: string;
  name: string;
  items: MarketAsset[];
}

interface StoredWatchlist {
  version: 1;
  groups: WatchlistGroup[];
}

export const WATCHLIST_STORAGE_KEY = "forbiddenland.watchlist.v1";

const DEFAULT_GROUPS: WatchlistGroup[] = [
  {
    id: "core-observation",
    name: "重点观察",
    items: [
      { asset_type: "stock", code: "688256", name: "寒武纪" },
      { asset_type: "stock", code: "688072", name: "拓荆科技" },
      { asset_type: "stock", code: "600183", name: "生益科技" },
    ],
  },
];

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isAsset(value: unknown): value is MarketAsset {
  if (!isRecord(value)) return false;
  return (
    ["stock", "index", "concept"].includes(String(value.asset_type)) &&
    typeof value.code === "string" &&
    value.code.trim().length > 0 &&
    typeof value.name === "string" &&
    value.name.trim().length > 0
  );
}

function normalizeGroups(value: unknown): WatchlistGroup[] | null {
  if (!isRecord(value) || value.version !== 1 || !Array.isArray(value.groups)) return null;
  const groups: WatchlistGroup[] = [];
  for (const rawGroup of value.groups) {
    if (
      !isRecord(rawGroup) ||
      typeof rawGroup.id !== "string" ||
      !rawGroup.id ||
      typeof rawGroup.name !== "string" ||
      !rawGroup.name.trim() ||
      !Array.isArray(rawGroup.items)
    ) {
      continue;
    }
    const seen = new Set<string>();
    const items = rawGroup.items.filter(isAsset).filter((item) => {
      const key = `${item.asset_type}:${item.code}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    groups.push({
      id: rawGroup.id,
      name: rawGroup.name.trim().slice(0, 40),
      items,
    });
  }
  return groups.length > 0 ? groups : null;
}

export function createGroupId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `group-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function loadWatchlistGroups(storage: Storage = window.localStorage): WatchlistGroup[] {
  try {
    const raw = storage.getItem(WATCHLIST_STORAGE_KEY);
    if (!raw) return structuredClone(DEFAULT_GROUPS);
    return normalizeGroups(JSON.parse(raw)) ?? structuredClone(DEFAULT_GROUPS);
  } catch {
    return structuredClone(DEFAULT_GROUPS);
  }
}

export function saveWatchlistGroups(
  groups: WatchlistGroup[],
  storage: Storage = window.localStorage,
): void {
  const payload: StoredWatchlist = { version: 1, groups };
  storage.setItem(WATCHLIST_STORAGE_KEY, JSON.stringify(payload));
}
