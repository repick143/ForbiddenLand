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
`http://127.0.0.1:8000`. Start the backend from the repository root in another terminal:

```text
python -m forbiddenland.api.app
```

Create a production bundle with `npm run build`. Frontend state, formatting, and chart rendering
belong here; market-data access, calculations, provenance, and AKQuant execution belong to the
backend service.
