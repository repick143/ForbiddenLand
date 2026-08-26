# API Contracts

The FastAPI application is the source of truth for the backend contract. Export a reviewable
OpenAPI document from the repository root with:

```text
python scripts/export_openapi.py
```

The generated `openapi.json` is a lightweight control-plane artifact and may be committed. It
contains endpoint and schema definitions only; it never contains market data or credentials.

The browser application must use `/api/v1` endpoints and must not access DuckDB, Parquet, AkShare,
or AKQuant directly.
