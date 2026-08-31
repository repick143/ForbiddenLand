# API Contracts

The FastAPI application is the source of truth for the backend contract. Export a reviewable
OpenAPI document from the repository root with:

```text
python scripts/export_openapi.py
```

The generated `openapi.json` is a lightweight control-plane artifact and may be committed. It
contains endpoint and schema definitions only; it never contains market data or credentials.

The versioned `/api/v1` surface currently contains `/health`, `/market/securities`,
`/market/assets`, `/market/bars`, `/analysis/history`, and
`/analysis/history/{symbol}/{analysis_date}`. The history list returns compact rows with filtering
and explicit malformed-file warnings; the detail route returns the complete persisted record,
including the prior-analysis review and provenance. FastAPI response models define the wire shape;
the frontend must call these paths through `frontend/src/api/client.ts` and represent them in
`frontend/src/types.ts`. It must not access DuckDB, Parquet, the analysis-history filesystem,
AkShare, or AKQuant directly.

`/market/bars` provenance includes `cache_hit`. A `true` value means the remote provider served a
valid, unexpired normalized response from its rebuildable local cache; it does not mean the request
was switched to the reviewed-local backend.

Any request or response change is a coordinated contract change: update the backend route/schema,
regenerate this document, update the frontend client/types, and add or adjust mocked contract tests
before merging. Keep API errors explicit and preserve missing data as `null` where the schema allows
it.
