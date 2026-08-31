import { useEffect, useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  CalendarDays,
  CheckCircle2,
  ChevronRight,
  ClipboardCheck,
  Database,
  History as HistoryIcon,
  LoaderCircle,
  Minus,
  RefreshCw,
  Search,
  ShieldAlert,
  TrendingDown,
  TrendingUp,
  X,
} from "lucide-react";

import { getAnalysisHistory, getAnalysisRecord } from "../api/client";
import type {
  AnalysisHistorySummary,
  AnalysisPattern,
  AnalysisRecord,
  AnalysisStance,
} from "../types";

const STANCE_LABEL: Record<AnalysisStance, string> = {
  bullish: "偏多",
  neutral: "中性",
  bearish: "偏空",
};

const INDICATOR_LABEL: Record<string, string> = {
  latest_close: "最新收盘",
  return_5d_pct: "5 日涨跌",
  return_20d_pct: "20 日涨跌",
  return_4w_pct: "4 周涨跌",
  return_12w_pct: "12 周涨跌",
  sma20: "SMA20",
  sma50: "SMA50",
  sma200: "SMA200",
  sma10: "SMA10",
  sma40: "SMA40",
  sma20_slope_5d: "SMA20 5 日斜率",
  sma20_slope_4w: "SMA20 4 周斜率",
  rsi14: "RSI14",
  atr14: "ATR14",
  atr14_pct: "ATR14 %",
  volume_ratio20: "20 日量比",
  relative_position60_pct: "60 日位置",
  relative_position20_pct: "20 周位置",
};

function formatNumber(value: unknown, digits = 2): string {
  if (typeof value !== "number" || !Number.isFinite(value)) return "--";
  return value.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function asRecord(value: unknown): Record<string, unknown> {
  return typeof value === "object" && value !== null
    ? (value as Record<string, unknown>)
    : {};
}

function stanceIcon(stance: AnalysisStance) {
  if (stance === "bullish") return <TrendingUp size={14} />;
  if (stance === "bearish") return <TrendingDown size={14} />;
  return <Minus size={14} />;
}

function stanceClass(stance: AnalysisStance): string {
  return `analysis-stance analysis-stance-${stance}`;
}

function reviewLabel(status: string): string {
  if (status === "no_prior_analysis") return "首份记录";
  if (status === "reviewed") return "已复盘";
  return status;
}

function thesisLabel(status: string): string {
  const labels: Record<string, string> = {
    confirmed: "假设得到支持",
    invalidated: "假设失效",
    ambiguous: "顺序不明",
    pending: "仍待观察",
    not_available: "暂无前序记录",
  };
  return labels[status] ?? status;
}

function outcomeLabel(outcome: string): string {
  const labels: Record<string, string> = {
    target_touched: "触及目标",
    stop_loss_touched: "触及止损",
    both_levels_touched_order_unknown: "目标与止损均触及（顺序不明）",
    trigger_touched_pending: "触及触发价，待确认",
    not_triggered: "尚未触发",
    no_new_bars: "没有新增日线",
    not_applicable: "不适用",
  };
  return labels[outcome] ?? outcome;
}

function setupStatusLabel(status: string): string {
  const labels: Record<string, string> = {
    breakout_wait_for_retest: "突破后等回踩",
    wait_for_volume_breakout: "等待放量突破",
    long_setup_suspended: "多头观察暂停",
  };
  return labels[status] ?? status;
}

function timeframeLabel(value: string): string {
  if (value === "daily") return "日线";
  if (value === "weekly") return "周线";
  if (value === "weekly+daily") return "周线 + 日线";
  return value;
}

function patternStatusLabel(value: string): string {
  const labels: Record<string, string> = {
    aligned: "一致",
    mixed: "分歧",
    confirmed: "已确认",
    needs_confirmation: "待确认",
    weak_or_missing: "量能不足",
    range_or_transition: "区间/过渡",
    overbought: "偏热",
    oversold: "偏冷",
    neutral: "中性",
    not_applicable: "不适用",
    not_triggered: "未触发",
    insufficient_data: "数据不足",
  };
  return labels[value] ?? value;
}

function IndicatorGroup({ title, values }: { title: string; values: Record<string, unknown> }) {
  const fields = [
    "latest_close",
    "return_5d_pct",
    "return_20d_pct",
    "return_4w_pct",
    "return_12w_pct",
    "sma10",
    "sma20",
    "sma40",
    "sma50",
    "sma200",
    "rsi14",
    "atr14_pct",
    "volume_ratio20",
    "relative_position60_pct",
    "relative_position20_pct",
  ].filter((field) => field in values);
  return (
    <div className="analysis-indicator-group">
      <h4>{title}</h4>
      <dl className="analysis-metric-grid">
        {fields.map((field) => (
          <div key={field}>
            <dt>{INDICATOR_LABEL[field] ?? field}</dt>
            <dd>
              {formatNumber(values[field], field.includes("pct") || field.includes("return") ? 2 : 2)}
              {(field.includes("pct") || field.includes("return")) && values[field] !== null ? "%" : ""}
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}

function HistoryDetail({
  record,
  loading,
  onClose,
}: {
  record: AnalysisRecord | null;
  loading: boolean;
  onClose: () => void;
}) {
  const structure = asRecord(record?.structure);
  const trend = asRecord(structure.trend);
  const support = asRecord(structure.support);
  const resistance = asRecord(structure.resistance);
  const breakout = asRecord(structure.breakout);

  function levelLabel(level: Record<string, unknown>): string {
    const lower = formatNumber(level.lower);
    const upper = formatNumber(level.upper);
    return lower === "--" && upper === "--" ? "--" : `${lower} - ${upper}`;
  }

  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        className="analysis-detail"
        role="dialog"
        aria-modal="true"
        aria-labelledby="analysis-detail-title"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <header className="modal-header analysis-detail-header">
          <div>
            <p className="eyebrow">
              {record ? `${record.asset.code} / ${record.analysis_date}` : "ANALYSIS RECORD"}
            </p>
            <h2 id="analysis-detail-title">{record?.asset.name ?? "分析记录"}</h2>
          </div>
          {record && (
            <span className={stanceClass(record.stance)}>
              {stanceIcon(record.stance)} {STANCE_LABEL[record.stance]}
            </span>
          )}
          <button type="button" className="icon-button" title="关闭" aria-label="关闭" onClick={onClose}>
            <X size={19} />
          </button>
        </header>

        {loading ? (
          <div className="analysis-detail-loading"><LoaderCircle size={22} className="spin" /></div>
        ) : record ? (
          <div className="analysis-detail-body">
            <section className="analysis-detail-lead">
              <div>
                <p className="eyebrow">研究结论</p>
                <h3>{record.headline}</h3>
                <p>{record.summary}</p>
              </div>
              <div className="analysis-as-of">
                <CalendarDays size={15} />
                <span>行情截至 {record.as_of_date}</span>
                <strong>{formatNumber(record.latest_close)}</strong>
              </div>
            </section>

            <section className="analysis-review-panel">
              <header>
                <span><ClipboardCheck size={16} /> 复盘</span>
                <span className={`review-state review-state-${record.review.thesis_status}`}>
                  {thesisLabel(record.review.thesis_status)}
                </span>
              </header>
              <p>{record.review.summary}</p>
              <div className="analysis-review-meta">
                <span>结果：{outcomeLabel(record.review.outcome)}</span>
                {record.review.previous_analysis_date && (
                  <span>对比：{record.review.previous_analysis_date}</span>
                )}
                {record.review.period_start && record.review.period_end && (
                  <span>观察区间：{record.review.period_start} 至 {record.review.period_end}</span>
                )}
              </div>
              {record.review.checks.length > 0 && (
                <ul>
                  {record.review.checks.map((check) => <li key={check}>{check}</li>)}
                </ul>
              )}
            </section>

            <div className="analysis-detail-grid">
              <section className="analysis-section">
                <header><TrendingUp size={16} /><h3>指标</h3></header>
                <div className="analysis-indicator-columns">
                  <IndicatorGroup title="日线" values={asRecord(asRecord(record.indicators).daily)} />
                  <IndicatorGroup title="周线" values={asRecord(asRecord(record.indicators).weekly)} />
                </div>
              </section>

              <section className="analysis-section">
                <header><ShieldAlert size={16} /><h3>条件观察位</h3></header>
                <dl className="analysis-setup-grid">
                  <div><dt>状态</dt><dd>{setupStatusLabel(record.setup.status)}</dd></div>
                  <div><dt>触发价</dt><dd>{formatNumber(record.setup.trigger_price)}</dd></div>
                  <div><dt>止损</dt><dd>{formatNumber(record.setup.stop_loss)}</dd></div>
                  <div><dt>目标</dt><dd>{formatNumber(record.setup.target_price)}</dd></div>
                  <div><dt>风险收益</dt><dd>{formatNumber(record.setup.risk_reward)} R</dd></div>
                </dl>
                <p className="analysis-invalidation"><strong>失效条件：</strong>{record.setup.invalidation}</p>
                <p className="analysis-risk-note">{record.setup.risk_note}</p>
              </section>
            </div>

            <section className="analysis-section">
              <header><TrendingUp size={16} /><h3>结构位置</h3></header>
              <dl className="analysis-structure-grid">
                <div><dt>日线趋势</dt><dd>{patternStatusLabel(trend.daily as string ?? "--")}</dd></div>
                <div><dt>周线趋势</dt><dd>{patternStatusLabel(trend.weekly as string ?? "--")}</dd></div>
                <div><dt>支撑区间</dt><dd>{levelLabel(support)}</dd></div>
                <div><dt>阻力区间</dt><dd>{levelLabel(resistance)}</dd></div>
                <div><dt>突破方向</dt><dd>{breakout.direction === "up" ? "向上" : breakout.direction === "down" ? "向下" : "无"}</dd></div>
                <div><dt>突破状态</dt><dd>{patternStatusLabel(String(breakout.status ?? "--"))}</dd></div>
              </dl>
            </section>

            <section className="analysis-section">
              <header><CheckCircle2 size={16} /><h3>形态证据</h3></header>
              <div className="analysis-pattern-list">
                {record.patterns.map((pattern: AnalysisPattern) => (
                  <article className="analysis-pattern" key={`${pattern.name}-${pattern.timeframe}`}>
                    <div className="analysis-pattern-topline">
                      <strong>{pattern.name}</strong>
                      <span>{timeframeLabel(pattern.timeframe)} · {patternStatusLabel(pattern.status)}</span>
                    </div>
                    <p>{pattern.evidence}</p>
                    <small>成交量：{patternStatusLabel(pattern.volume_confirmation)}{pattern.confidence ? ` · 置信度 ${pattern.confidence}` : ""}</small>
                  </article>
                ))}
              </div>
            </section>

            <section className="analysis-section analysis-provenance-section">
              <header><Database size={16} /><h3>来源与验证</h3></header>
              <dl className="analysis-provenance-grid">
                <div><dt>来源</dt><dd>{record.provenance.source}</dd></div>
                <div><dt>后端 / 存储</dt><dd>{record.provenance.backend} / {record.provenance.storage}</dd></div>
                <div><dt>数据窗口</dt><dd>{record.provenance.start_date} 至 {record.provenance.end_date}</dd></div>
                <div><dt>复权 / 频率</dt><dd>{record.provenance.adjust || "不复权"} / {record.provenance.frequency}</dd></div>
                <div><dt>样本</dt><dd>{record.validation.sample_size_bars} 根日线 · {record.validation.backtest_available ? "有回测" : "未回测"}</dd></div>
                <div><dt>抓取时间</dt><dd>{record.provenance.retrieved_at_utc ?? "--"}</dd></div>
                <div><dt>缓存</dt><dd>{record.provenance.cache_hit ? "命中远端缓存" : "实时获取"}</dd></div>
                <div><dt>有效日线</dt><dd>{record.provenance.bar_count}</dd></div>
              </dl>
              {record.validation.warnings.length > 0 && (
                <ul className="analysis-warning-list">
                  {record.validation.warnings.map((warning) => <li key={warning}>{warning}</li>)}
                </ul>
              )}
            </section>
          </div>
        ) : null}
      </section>
    </div>
  );
}

export function AnalysisHistoryView() {
  const [query, setQuery] = useState("");
  const [symbol, setSymbol] = useState("");
  const [items, setItems] = useState<AnalysisHistorySummary[]>([]);
  const [total, setTotal] = useState(0);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshToken, setRefreshToken] = useState(0);
  const [selected, setSelected] = useState<AnalysisRecord | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const detailController = useRef<AbortController | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError(null);
      getAnalysisHistory({ query, symbol, limit: 200, signal: controller.signal })
        .then((response) => {
          setItems(response.items);
          setTotal(response.total);
          setWarnings(response.warnings);
        })
        .catch((reason: unknown) => {
          if (reason instanceof DOMException && reason.name === "AbortError") return;
          setItems([]);
          setWarnings([]);
          setError(reason instanceof Error ? reason.message : "无法读取分析历史");
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        });
    }, 120);
    return () => {
      window.clearTimeout(timer);
      controller.abort();
    };
  }, [query, refreshToken, symbol]);

  useEffect(() => () => detailController.current?.abort(), []);

  const stockOptions = useMemo(() => {
    const byCode = new Map<string, string>();
    items.forEach((item) => byCode.set(item.asset.code, item.asset.name));
    return Array.from(byCode, ([code, name]) => ({ code, name })).sort((a, b) => a.code.localeCompare(b.code));
  }, [items]);

  const groups = useMemo(() => {
    const grouped = new Map<string, { code: string; name: string; items: AnalysisHistorySummary[] }>();
    items.forEach((item) => {
      const key = item.asset.code;
      const group = grouped.get(key) ?? { code: key, name: item.asset.name, items: [] };
      group.items.push(item);
      grouped.set(key, group);
    });
    return Array.from(grouped.values()).sort((a, b) => a.code.localeCompare(b.code));
  }, [items]);

  function openRecord(item: AnalysisHistorySummary) {
    detailController.current?.abort();
    const controller = new AbortController();
    detailController.current = controller;
    setSelected(null);
    setDetailLoading(true);
    getAnalysisRecord(item.asset.code, item.analysis_date, controller.signal)
      .then(setSelected)
      .catch((reason: unknown) => {
        if (reason instanceof DOMException && reason.name === "AbortError") return;
        setError(reason instanceof Error ? reason.message : "无法读取分析详情");
      })
      .finally(() => {
        if (!controller.signal.aborted) setDetailLoading(false);
      });
  }

  function closeDetail() {
    detailController.current?.abort();
    setSelected(null);
    setDetailLoading(false);
  }

  return (
    <main className="history-workspace" aria-labelledby="history-title">
      <section className="history-heading">
        <div>
          <p className="eyebrow">ANALYSIS JOURNAL / DAILY PARTITIONS</p>
          <h2 id="history-title">分析历史</h2>
          <span>{total} 条记录 · 按个股与分析日聚合</span>
        </div>
        <button
          type="button"
          className="icon-button history-refresh-button"
          title="刷新分析历史"
          aria-label="刷新分析历史"
          onClick={() => setRefreshToken((value) => value + 1)}
        >
          <RefreshCw size={17} />
        </button>
      </section>

      <section className="history-toolbar" aria-label="分析历史筛选">
        <label className="history-search-field">
          <Search size={16} />
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索股票、结论或复盘"
            aria-label="搜索股票、结论或复盘"
          />
        </label>
        <label className="history-stock-filter">
          <span>个股</span>
          <select value={symbol} onChange={(event) => setSymbol(event.target.value)}>
            <option value="">全部个股</option>
            {stockOptions.map((option) => <option key={option.code} value={option.code}>{option.name} · {option.code}</option>)}
          </select>
        </label>
      </section>

      {error && (
        <div className="notice error-notice" role="alert"><AlertCircle size={17} /><span>{error}</span></div>
      )}
      {warnings.length > 0 && (
        <div className="notice warning-notice" role="status"><ShieldAlert size={17} /><span>{warnings.length} 个历史文件需要检查</span></div>
      )}

      {loading ? (
        <div className="history-loading"><LoaderCircle size={23} className="spin" /><span>正在读取分析历史</span></div>
      ) : groups.length === 0 ? (
        <section className="empty-state history-empty-state">
          <div className="empty-icon"><HistoryIcon size={22} /></div>
          <h3>暂无分析记录</h3>
          <p>完成一次个股分析后，记录会按股票代码和分析日保存。</p>
        </section>
      ) : (
        <div className="history-groups">
          {groups.map((group) => (
            <section className="history-stock-group" key={group.code}>
              <header className="history-stock-heading">
                <div>
                  <span className="asset-type asset-type-stock"><HistoryIcon size={14} /> 个股</span>
                  <h3>{group.name}</h3>
                  <small>{group.code}</small>
                </div>
                <span>{group.items.length} 次分析</span>
              </header>
              <div className="history-entry-list">
                {group.items.map((item) => (
                  <button
                    type="button"
                    className="history-entry"
                    key={item.analysis_id}
                    onClick={() => openRecord(item)}
                  >
                    <div className="history-entry-date">
                      <strong>{item.analysis_date}</strong>
                      <small>行情截至 {item.as_of_date}</small>
                    </div>
                    <div className="history-entry-main">
                      <div className="history-entry-meta">
                        <span className={stanceClass(item.stance)}>{stanceIcon(item.stance)} {STANCE_LABEL[item.stance]}</span>
                        <span className="review-chip"><ClipboardCheck size={12} /> {reviewLabel(item.review_status)}</span>
                        {item.cache_hit && <span className="cache-chip">缓存</span>}
                      </div>
                      <h4>{item.headline}</h4>
                      <p>{item.summary}</p>
                    </div>
                    <ChevronRight size={17} className="history-entry-arrow" />
                  </button>
                ))}
              </div>
            </section>
          ))}
        </div>
      )}

      {(selected || detailLoading) && <HistoryDetail record={selected} loading={detailLoading} onClose={closeDetail} />}
    </main>
  );
}
