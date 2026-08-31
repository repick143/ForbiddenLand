# ForbiddenLand Frontend

This is the independent React/Vite/TypeScript presentation project. It talks to the Python
backend through the versioned `/api/v1` contract and never opens DuckDB, Parquet, or AkShare
directly.

Use Node `22.14.x` (the version is recorded in `.node-version` and `package.json`) on Ubuntu Linux.
From the repository root, initialize the Python and frontend environments first:

```text
python scripts/bootstrap.py
```

The default `full` profile installs this project's `node_modules` with `npm ci`. From the repository
root, the recommended development command starts both services with one shared API target:

```text
bash scripts/start.sh
```

Pass `--api-port 9093` when a different backend port is needed. The launcher injects
`FORBIDDENLAND_API_PORT` and `FORBIDDENLAND_API_PROXY_TARGET` into both processes, so the browser
proxy and FastAPI listener cannot drift apart.

`scripts/dev.py` remains available when the Python process orchestrator is needed directly.

For frontend-only work, install and run Vite directly:

```text
npm ci
npm run dev
```

The Vite development server runs on `http://127.0.0.1:5173` and proxies versioned `/api/v1`
requests to the backend at `http://127.0.0.1:9092` by default. Start the backend from the
repository root in another terminal:

```text
python -m forbiddenland.api.app
```

The backend development entry point enables Uvicorn auto-reload by default, so Python/API changes
are picked up without manually restarting the process. Set `FORBIDDENLAND_API_RELOAD=0` only when
you intentionally need a non-reloading process. Changes to Vite configuration, package metadata,
or environment variables still require restarting `npm run dev`.

When services are started separately, set `FORBIDDENLAND_API_PORT` to the backend port and use
`FORBIDDENLAND_API_PROXY_TARGET` for a non-default host or a complete URL. The proxy target takes
precedence over the port variable; restart Vite after changing either value.

The watchlist workspace defaults to the local calendar date one month earlier through today. It
supports browser-local groups, stock/index/concept search, 4/6/9 item pages, compact daily
candlestick charts, and an expanded daily candlestick/volume view. Watchlists are stored under
`forbiddenland.watchlist.v1` in `localStorage`; they are not written to DuckDB or Git.

Charts use the official `lightweight-charts` package with its required TradingView attribution.
The backend supplies stock and audited Tonghuashun concept data in local mode. Broad-index history
uses remote AkShare until a reviewed local index snapshot is available.

The top navigation has a `量价方法` tab backed by
[`src/content/volume_price_analysis.md`](src/content/volume_price_analysis.md). The guide compares
VSA, Wyckoff, VPA, and Volume Profile, and records the current daily-only data boundary and the
fields needed for a future signal layer. It is bundled with the frontend and does not require a
market-data request.

The `分析历史` tab reads the versioned analysis journal API. It lists records grouped by stock and
sorted by analysis date, supports keyword and stock filtering, and opens a detail view with daily /
weekly indicators, conditional trigger/stop/target levels, the previous-record review, provenance,
and validation warnings. The frontend never reads `analysis_history/` directly; use
`frontend/src/api/client.ts` and `frontend/src/types.ts` when the contract changes.

During the current development phase, use `npm run dev` and rely on Vite HMR; do not use a
production bundle for local verification. `npm run build` remains available for a later release
workflow. Frontend state, formatting, and chart rendering belong here; market-data access,
calculations, provenance, and AKQuant execution belong to the backend service.
