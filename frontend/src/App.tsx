import { FormEvent, useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  Check,
  ChevronLeft,
  ChevronRight,
  Database,
  FolderPlus,
  Pencil,
  Plus,
  RefreshCw,
  Server,
  Trash2,
  X,
} from "lucide-react";

import { getHealth } from "./api/client";
import { AssetCard } from "./components/AssetCard";
import { AssetDetailDialog } from "./components/AssetDetailDialog";
import { AssetPicker } from "./components/AssetPicker";
import { getDefaultDateRange } from "./dateRange";
import type { HealthResponse, MarketAsset, MarketBarsResponse } from "./types";
import {
  createGroupId,
  loadWatchlistGroups,
  saveWatchlistGroups,
  type WatchlistGroup,
} from "./watchlist";

const DEFAULT_DATE_RANGE = getDefaultDateRange();

interface DetailState {
  asset: MarketAsset;
  market: MarketBarsResponse;
}

function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [serviceError, setServiceError] = useState<string | null>(null);
  const [groups, setGroups] = useState<WatchlistGroup[]>(loadWatchlistGroups);
  const [activeGroupId, setActiveGroupId] = useState(() => groups[0].id);
  const [groupEditor, setGroupEditor] = useState<"create" | "rename" | null>(null);
  const [groupName, setGroupName] = useState("");
  const [pickerOpen, setPickerOpen] = useState(false);
  const [detail, setDetail] = useState<DetailState | null>(null);
  const [draftStartDate, setDraftStartDate] = useState(DEFAULT_DATE_RANGE.startDate);
  const [draftEndDate, setDraftEndDate] = useState(DEFAULT_DATE_RANGE.endDate);
  const [startDate, setStartDate] = useState(DEFAULT_DATE_RANGE.startDate);
  const [endDate, setEndDate] = useState(DEFAULT_DATE_RANGE.endDate);
  const [adjust, setAdjust] = useState<"" | "qfq" | "hfq">("qfq");
  const [pageSize, setPageSize] = useState(6);
  const [page, setPage] = useState(1);
  const [refreshToken, setRefreshToken] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    getHealth()
      .then((response) => {
        setHealth(response);
        setServiceError(null);
      })
      .catch((reason: unknown) => {
        setServiceError(reason instanceof Error ? reason.message : "无法连接后端服务");
      });
  }, []);

  useEffect(() => {
    try {
      saveWatchlistGroups(groups);
    } catch {
      setNotice("浏览器无法保存自选分组");
    }
  }, [groups]);

  const activeGroup = groups.find((group) => group.id === activeGroupId) ?? groups[0];
  const pageCount = Math.max(1, Math.ceil(activeGroup.items.length / pageSize));
  const currentPage = Math.min(page, pageCount);
  const visibleItems = activeGroup.items.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize,
  );
  const existingKeys = useMemo(
    () => new Set(activeGroup.items.map((item) => `${item.asset_type}:${item.code}`)),
    [activeGroup.items],
  );

  useEffect(() => {
    setPage(1);
    setDetail(null);
  }, [activeGroupId, pageSize, startDate, endDate, adjust]);

  function updateActiveGroup(update: (group: WatchlistGroup) => WatchlistGroup) {
    setGroups((current) =>
      current.map((group) => (group.id === activeGroup.id ? update(group) : group)),
    );
  }

  function openGroupEditor(mode: "create" | "rename") {
    setGroupName(mode === "rename" ? activeGroup.name : "");
    setGroupEditor(mode);
  }

  function submitGroup(event: FormEvent) {
    event.preventDefault();
    const name = groupName.trim().slice(0, 40);
    if (!name) return;
    if (groupEditor === "create") {
      const group = { id: createGroupId(), name, items: [] };
      setGroups((current) => [...current, group]);
      setActiveGroupId(group.id);
    } else if (groupEditor === "rename") {
      updateActiveGroup((group) => ({ ...group, name }));
    }
    setGroupEditor(null);
    setGroupName("");
  }

  function deleteActiveGroup() {
    if (groups.length === 1) return;
    if (!window.confirm(`删除分组“${activeGroup.name}”？`)) return;
    const remaining = groups.filter((group) => group.id !== activeGroup.id);
    setGroups(remaining);
    setActiveGroupId(remaining[0].id);
  }

  function addAsset(asset: MarketAsset) {
    const key = `${asset.asset_type}:${asset.code}`;
    if (existingKeys.has(key)) return;
    updateActiveGroup((group) => ({ ...group, items: [...group.items, asset] }));
    setPage(Math.ceil((activeGroup.items.length + 1) / pageSize));
  }

  function removeAsset(asset: MarketAsset) {
    updateActiveGroup((group) => ({
      ...group,
      items: group.items.filter(
        (item) => item.asset_type !== asset.asset_type || item.code !== asset.code,
      ),
    }));
  }

  function applyQuery() {
    if (!draftStartDate || !draftEndDate || draftStartDate > draftEndDate) {
      setNotice("请选择有效的行情日期区间");
      return;
    }
    setNotice(null);
    setStartDate(draftStartDate);
    setEndDate(draftEndDate);
    setRefreshToken((value) => value + 1);
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <Activity size={18} strokeWidth={2.4} />
          </div>
          <div>
            <p className="eyebrow">FORBIDDENLAND / WATCHLIST</p>
            <h1>自选研究台</h1>
          </div>
        </div>
        <div className={`service-status ${health ? "is-online" : ""}`}>
          <span className="status-dot" />
          {health ? "API 在线" : "等待 API"}
        </div>
      </header>

      <div className="workspace-layout">
        <aside className="watchlist-sidebar" aria-label="自选分组">
          <div className="sidebar-heading">
            <span>自选分组</span>
            <button
              type="button"
              className="icon-button sidebar-icon-button"
              title="创建分组"
              aria-label="创建分组"
              onClick={() => openGroupEditor("create")}
            >
              <FolderPlus size={17} />
            </button>
          </div>

          <nav className="group-list">
            {groups.map((group) => (
              <button
                key={group.id}
                type="button"
                className={group.id === activeGroup.id ? "group-button is-active" : "group-button"}
                onClick={() => setActiveGroupId(group.id)}
              >
                <span>{group.name}</span>
                <small>{group.items.length}</small>
              </button>
            ))}
          </nav>

          {groupEditor && (
            <form className="group-editor" onSubmit={submitGroup}>
              <input
                autoFocus
                maxLength={40}
                value={groupName}
                onChange={(event) => setGroupName(event.target.value)}
                aria-label={groupEditor === "create" ? "新分组名称" : "分组名称"}
              />
              <button type="submit" className="icon-button" title="确认" aria-label="确认">
                <Check size={16} />
              </button>
              <button
                type="button"
                className="icon-button"
                title="取消"
                aria-label="取消"
                onClick={() => setGroupEditor(null)}
              >
                <X size={16} />
              </button>
            </form>
          )}

          <div className="group-actions">
            <button type="button" onClick={() => openGroupEditor("rename")}>
              <Pencil size={14} /> 重命名
            </button>
            <button type="button" onClick={deleteActiveGroup} disabled={groups.length === 1}>
              <Trash2 size={14} /> 删除
            </button>
          </div>

          <div className="service-meta sidebar-service-meta">
            <span><Server size={14} /> {health?.service ?? "forbiddenland-api"}</span>
            <span><Database size={14} /> {health?.backend ?? "--"}</span>
          </div>
        </aside>

        <main className="watchlist-workspace">
          <section className="workspace-heading">
            <div>
              <p className="eyebrow">MARKET WATCH</p>
              <h2>{activeGroup.name}</h2>
              <span>{activeGroup.items.length} 个标的</span>
            </div>
            <button type="button" className="primary-button" onClick={() => setPickerOpen(true)}>
              <Plus size={17} /> 添加标的
            </button>
          </section>

          <section className="query-toolbar" aria-label="行情展示条件">
            <label>
              <span>起始日期</span>
              <input
                type="date"
                value={draftStartDate}
                onChange={(event) => setDraftStartDate(event.target.value)}
              />
            </label>
            <label>
              <span>结束日期</span>
              <input
                type="date"
                value={draftEndDate}
                onChange={(event) => setDraftEndDate(event.target.value)}
              />
            </label>
            <label>
              <span>个股复权</span>
              <select value={adjust} onChange={(event) => setAdjust(event.target.value as typeof adjust)}>
                <option value="qfq">前复权</option>
                <option value="hfq">后复权</option>
                <option value="">不复权</option>
              </select>
            </label>
            <label>
              <span>每页展示</span>
              <select value={pageSize} onChange={(event) => setPageSize(Number(event.target.value))}>
                <option value={4}>4 个</option>
                <option value={6}>6 个</option>
                <option value={9}>9 个</option>
              </select>
            </label>
            <button type="button" className="refresh-button" onClick={applyQuery}>
              <RefreshCw size={16} /> 刷新行情
            </button>
          </section>

          {(serviceError || notice) && (
            <div className="notice error-notice" role="alert">
              <AlertCircle size={17} />
              <span>{notice ?? serviceError}</span>
            </div>
          )}

          {activeGroup.items.length === 0 ? (
            <section className="empty-state">
              <div className="empty-icon"><Activity size={22} /></div>
              <h3>此分组暂无标的</h3>
              <button type="button" className="secondary-button" onClick={() => setPickerOpen(true)}>
                <Plus size={16} /> 添加标的
              </button>
            </section>
          ) : (
            <>
              <section className="asset-grid" aria-live="polite">
                {visibleItems.map((asset) => (
                  <AssetCard
                    key={`${asset.asset_type}:${asset.code}`}
                    asset={asset}
                    startDate={startDate}
                    endDate={endDate}
                    adjust={adjust}
                    refreshToken={refreshToken}
                    onOpen={(selectedAsset, market) => setDetail({ asset: selectedAsset, market })}
                    onRemove={removeAsset}
                  />
                ))}
              </section>

              <nav className="pagination" aria-label="自选行情分页">
                <button
                  type="button"
                  className="icon-button"
                  title="上一页"
                  aria-label="上一页"
                  disabled={currentPage === 1}
                  onClick={() => setPage((value) => Math.max(1, value - 1))}
                >
                  <ChevronLeft size={18} />
                </button>
                <span>{currentPage} / {pageCount}</span>
                <button
                  type="button"
                  className="icon-button"
                  title="下一页"
                  aria-label="下一页"
                  disabled={currentPage === pageCount}
                  onClick={() => setPage((value) => Math.min(pageCount, value + 1))}
                >
                  <ChevronRight size={18} />
                </button>
              </nav>
            </>
          )}
        </main>
      </div>

      <AssetPicker
        open={pickerOpen}
        existingKeys={existingKeys}
        onAdd={addAsset}
        onClose={() => setPickerOpen(false)}
      />
      {detail && (
        <AssetDetailDialog
          asset={detail.asset}
          market={detail.market}
          onClose={() => setDetail(null)}
        />
      )}
    </div>
  );
}

export default App;
