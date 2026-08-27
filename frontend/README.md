# ForbiddenLand Frontend

This is the independent React/Vite/TypeScript presentation project. It talks to the Python
backend through the versioned `/api/v1` contract and never opens DuckDB, Parquet, or AkShare
directly.

Use Node `22.14.x` (the version is recorded in `.node-version` and `package.json`):

```text
npm install
npm run dev
```

The Vite development server runs on `http://127.0.0.1:5173` and proxies `/api` to the backend at
`http://127.0.0.1:9092`. Start the backend from the repository root in another terminal:

```text
python -m forbiddenland.api.app
```

Set `FORBIDDENLAND_API_PROXY_TARGET` when the backend is running on another port; the default proxy
target remains `http://127.0.0.1:9092`.

The watchlist workspace defaults to the local calendar date one month earlier through today. It
supports browser-local groups, stock/index/concept search, 4/6/9 item pages, compact daily
candlestick charts, and an expanded daily candlestick/volume view. Watchlists are stored under
`forbiddenland.watchlist.v1` in `localStorage`; they are not written to DuckDB or Git.

Charts use the official `lightweight-charts` package with its required TradingView attribution.
The backend supplies stock and audited Tonghuashun concept data in local mode. Broad-index history
uses remote AkShare until a reviewed local index snapshot is available.

Create a production bundle with `npm run build`. Frontend state, formatting, and chart rendering
belong here; market-data access, calculations, provenance, and AKQuant execution belong to the
backend service.
