import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  AlertCircle,
  ArrowDownRight,
  ArrowUpRight,
  Database,
  RefreshCw,
  Server,
} from "lucide-react";

import { getHealth, getMarketBars, getSecurities } from "./api/client";
import type { HealthResponse, MarketBarsResponse, Security } from "./types";

const DEFAULT_START = "2024-01-01";
const DEFAULT_END = "2024-03-31";

function formatNumber(value: number | null, digits = 2): string {
  return value === null ? "--" : value.toLocaleString("zh-CN", { maximumFractionDigits: digits });
}

function PriceChart({ bars }: { bars: MarketBarsResponse["bars"] }) {
  const points = useMemo(() => {
    if (bars.length < 2) return "";
    const width = 760;
    const height = 220;
    const padding = 18;
    const values = bars.map((bar) => bar.close);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    return bars
      .map((bar, index) => {
        const x = padding + (index / (bars.length - 1)) * (width - padding * 2);
        const y = height - padding - ((bar.close - min) / span) * (height - padding * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
      })
      .join(" ");
  }, [bars]);

  if (!points) {
    return <div className="chart-empty">选择一个有数据的区间</div>;
  }
  return (
    <svg className="price-chart" viewBox="0 0 760 220" role="img" aria-label="收盘价走势">
      <line x1="18" y1="202" x2="742" y2="202" className="chart-axis" />
      <polyline points={points} className="chart-line" />
    </svg>
  );
}

function App() {
  const [health, setHealth] = useState<HealthResponse | null>(null);
  const [securities, setSecurities] = useState<Security[]>([]);
  const [selectedSymbol, setSelectedSymbol] = useState("688256");
  const [startDate, setStartDate] = useState(DEFAULT_START);
  const [endDate, setEndDate] = useState(DEFAULT_END);
  const [adjust, setAdjust] = useState<"" | "qfq" | "hfq">("qfq");
  const [market, setMarket] = useState<MarketBarsResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([getHealth(), getSecurities()])
      .then(([healthResponse, securitiesResponse]) => {
        setHealth(healthResponse);
        setSecurities(securitiesResponse.items);
      })
      .catch((reason: unknown) => {
        setError(reason instanceof Error ? reason.message : "无法连接后端服务");
      });
  }, []);

  const selectedSecurity = securities.find((item) => item.code === selectedSymbol);
  const periodChange = market?.summary.period_change_percent ?? null;
  const isPositive = periodChange !== null && periodChange >= 0;

  async function loadMarket() {
    setLoading(true);
    setError(null);
    try {
      setMarket(await getMarketBars(selectedSymbol, startDate, endDate, adjust));
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : "行情请求失败");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <div className="brand-lockup">
          <div className="brand-mark" aria-hidden="true">
            <Activity size={18} strokeWidth={2.4} />
          </div>
          <div>
            <p className="eyebrow">FORBIDDENLAND / RESEARCH</p>
            <h1>Research Desk</h1>
          </div>
        </div>
        <div className={`service-status ${health ? "is-online" : ""}`}>
          <span className="status-dot" />
          {health ? "API 在线" : "等待 API"}
        </div>
      </header>

      <main className="workspace">
        <section className="intro-band">
          <div>
            <p className="eyebrow">MARKET OVERVIEW</p>
            <h2>行情观察</h2>
          </div>
          <div className="service-meta">
            <span>
              <Server size={14} /> {health?.service ?? "forbiddenland-api"}
            </span>
            <span>
              <Database size={14} /> {health?.backend ?? "--"}
            </span>
          </div>
        </section>

        <section className="control-strip" aria-label="行情查询条件">
          <label>
            <span>股票</span>
            <select value={selectedSymbol} onChange={(event) => setSelectedSymbol(event.target.value)}>
              {securities.length === 0 && <option value="688256">688256 寒武纪</option>}
              {securities.map((security) => (
                <option key={security.code} value={security.code}>
                  {security.code} {security.name}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>起始日期</span>
            <input type="date" value={startDate} onChange={(event) => setStartDate(event.target.value)} />
          </label>
          <label>
            <span>结束日期</span>
            <input type="date" value={endDate} onChange={(event) => setEndDate(event.target.value)} />
          </label>
          <label>
            <span>复权</span>
            <select value={adjust} onChange={(event) => setAdjust(event.target.value as typeof adjust)}>
              <option value="qfq">前复权</option>
              <option value="hfq">后复权</option>
              <option value="">不复权</option>
            </select>
          </label>
          <button className="primary-button" type="button" onClick={loadMarket} disabled={loading}>
            <RefreshCw size={16} className={loading ? "spin" : ""} />
            {loading ? "读取中" : "读取行情"}
          </button>
        </section>

        {error && (
          <div className="notice error-notice" role="alert">
            <AlertCircle size={17} />
            <span>{error}</span>
          </div>
        )}

        {!market ? (
          <section className="empty-state">
            <div className="empty-icon"><Activity size={22} /></div>
            <h3>{selectedSecurity ? `${selectedSecurity.name} · ${selectedSymbol}` : "选择股票"}</h3>
            <p>读取行情后，价格区间、来源和数据质量会显示在这里。</p>
          </section>
        ) : (
          <>
            <section className="metric-grid">
              <article className="metric-card">
                <span>最新收盘</span>
                <strong>{formatNumber(market.summary.latest_close)}</strong>
                <small>{market.summary.latest_date}</small>
              </article>
              <article className={`metric-card ${isPositive ? "positive" : "negative"}`}>
                <span>区间涨跌</span>
                <strong>{periodChange === null ? "--" : `${isPositive ? "+" : ""}${formatNumber(periodChange)}%`}</strong>
                <small>{isPositive ? <ArrowUpRight size={14} /> : <ArrowDownRight size={14} />} {market.summary.bar_count} 根日线</small>
              </article>
              <article className="metric-card">
                <span>区间高点</span>
                <strong>{formatNumber(market.summary.max_close)}</strong>
                <small>收盘价</small>
              </article>
              <article className="metric-card">
                <span>区间低点</span>
                <strong>{formatNumber(market.summary.min_close)}</strong>
                <small>收盘价</small>
              </article>
            </section>

            <section className="analysis-grid">
              <article className="panel chart-panel">
                <div className="panel-heading">
                  <div>
                    <p className="eyebrow">CLOSE PRICE</p>
                    <h3>{selectedSecurity?.name ?? market.symbol} · 收盘走势</h3>
                  </div>
                  <span className="source-label">{market.provenance.source}</span>
                </div>
                <PriceChart bars={market.bars} />
              </article>
              <article className="panel provenance-panel">
                <div className="panel-heading">
                  <div>
                    <p className="eyebrow">PROVENANCE</p>
                    <h3>数据信息</h3>
                  </div>
                </div>
                <dl>
                  <div><dt>查询区间</dt><dd>{market.provenance.start_date} 至 {market.provenance.end_date}</dd></div>
                  <div><dt>复权方式</dt><dd>{market.provenance.adjust || "不复权"}</dd></div>
                  <div><dt>存储路径</dt><dd>{market.provenance.storage}</dd></div>
                  <div><dt>获取时间</dt><dd>{new Date(market.provenance.retrieved_at_utc).toLocaleString("zh-CN")}</dd></div>
                  <div><dt>本地快照</dt><dd>{market.provenance.local_snapshot_review_required ? "待复核" : "未使用"}</dd></div>
                </dl>
              </article>
            </section>

            <section className="panel table-panel">
              <div className="panel-heading">
                <div>
                  <p className="eyebrow">OBSERVATIONS</p>
                  <h3>最近交易日</h3>
                </div>
                <span className="table-count">显示最近 {Math.min(10, market.bars.length)} 条</span>
              </div>
              <div className="table-scroll">
                <table>
                  <thead><tr><th>日期</th><th>开盘</th><th>最高</th><th>最低</th><th>收盘</th><th>成交量</th><th>涨跌幅</th></tr></thead>
                  <tbody>
                    {market.bars.slice(-10).reverse().map((bar) => (
                      <tr key={bar.date}>
                        <td>{bar.date}</td><td>{formatNumber(bar.open)}</td><td>{formatNumber(bar.high)}</td>
                        <td>{formatNumber(bar.low)}</td><td className="close-cell">{formatNumber(bar.close)}</td>
                        <td>{formatNumber(bar.volume, 0)}</td><td>{bar.change_percent === null ? "--" : `${bar.change_percent >= 0 ? "+" : ""}${formatNumber(bar.change_percent)}%`}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
          </>
        )}
      </main>
    </div>
  );
}

export default App;
