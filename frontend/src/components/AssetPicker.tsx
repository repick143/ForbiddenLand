import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Check, LoaderCircle, Plus, Search, X } from "lucide-react";

import { getAssets } from "../api/client";
import type { AssetType, MarketAsset } from "../types";

interface AssetPickerProps {
  open: boolean;
  existingKeys: Set<string>;
  onAdd: (asset: MarketAsset) => void;
  onClose: () => void;
}

const ASSET_TABS: Array<{ value: AssetType; label: string }> = [
  { value: "stock", label: "个股" },
  { value: "index", label: "指数" },
  { value: "concept", label: "概念" },
];

export function AssetPicker({ open, existingKeys, onAdd, onClose }: AssetPickerProps) {
  const [assetType, setAssetType] = useState<AssetType>("stock");
  const [query, setQuery] = useState("");
  const [items, setItems] = useState<MarketAsset[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return undefined;
    const controller = new AbortController();
    const timeout = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      getAssets(assetType, query, controller.signal)
        .then((response) => setItems(response.items))
        .catch((reason: unknown) => {
          if (reason instanceof DOMException && reason.name === "AbortError") return;
          setItems([]);
          setError(reason instanceof Error ? reason.message : "无法读取标的目录");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 180);
    return () => {
      window.clearTimeout(timeout);
      controller.abort();
    };
  }, [assetType, open, query]);

  useEffect(() => {
    if (!open) return undefined;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [onClose, open]);

  const resultLabel = useMemo(() => {
    if (loading) return "正在读取";
    if (error) return "读取失败";
    return `${items.length} 个结果`;
  }, [error, items.length, loading]);

  if (!open) return null;

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="asset-picker"
        role="dialog"
        aria-modal="true"
        aria-labelledby="asset-picker-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="modal-header">
          <div>
            <p className="eyebrow">ADD ASSET</p>
            <h2 id="asset-picker-title">添加标的</h2>
          </div>
          <button type="button" className="icon-button" title="关闭" aria-label="关闭" onClick={onClose}>
            <X size={18} />
          </button>
        </header>

        <div className="asset-tabs" role="tablist" aria-label="标的类型">
          {ASSET_TABS.map((tab) => (
            <button
              key={tab.value}
              type="button"
              role="tab"
              aria-selected={assetType === tab.value}
              className={assetType === tab.value ? "is-active" : ""}
              onClick={() => setAssetType(tab.value)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        <label className="search-field">
          <Search size={16} />
          <input
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索名称或代码"
          />
        </label>

        <div className="picker-result-meta">{resultLabel}</div>
        <div className="asset-results">
          {loading ? (
            <div className="picker-placeholder"><LoaderCircle size={20} className="spin" /></div>
          ) : error ? (
            <div className="picker-placeholder picker-error"><AlertCircle size={18} /> {error}</div>
          ) : items.length === 0 ? (
            <div className="picker-placeholder">没有匹配标的</div>
          ) : (
            items.map((asset) => {
              const key = `${asset.asset_type}:${asset.code}`;
              const added = existingKeys.has(key);
              return (
                <button
                  key={key}
                  type="button"
                  className="asset-result"
                  disabled={added}
                  onClick={() => onAdd(asset)}
                >
                  <span><strong>{asset.name}</strong><small>{asset.code}</small></span>
                  {added ? <Check size={17} /> : <Plus size={17} />}
                </button>
              );
            })
          )}
        </div>
      </section>
    </div>
  );
}
