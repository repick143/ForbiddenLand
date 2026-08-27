# AGENTS.md

This file defines repository-level instructions for Codex and other coding agents. Keep it
focused on durable engineering constraints. Add project-specific rules as the codebase evolves.

## Project Scope

- This is a personal A-share research and analysis project implemented in Python.
- Treat generated analysis as research output, not investment advice.
- Prefer reproducible calculations and explicit data-quality reporting over opaque conclusions.
- Do not introduce business rules, scoring weights, or trading assumptions unless the task defines
  them or an existing project document records them.

## Python Environment

- Use the Python version declared in `.python-version`; do not change the user's global pyenv
  configuration.
- The repository development baseline is Python 3.12.10. Run `python scripts/bootstrap.py` to
  create or initialize the local environment.
- Keep the package compatible with the `requires-python` range in `pyproject.toml`.
- Install project dependencies with `python -m pip install -e ".[dev,data]"` when needed.
- Add runtime and development dependencies to the appropriate group in `pyproject.toml`.
- Ask before adding a new production dependency when the standard library or an existing
  dependency can reasonably solve the problem.

## Quantitative Core

- AKQuant (`akquant`) is the project's core quantitative research and backtesting engine. Do not
  replace it with another engine without an explicit project decision.
- AKShare (`akshare`) is used for data acquisition only; it is not the backtesting engine or the
  canonical storage layer.
- Use DuckDB as the default local analytical storage and query layer. Use Parquet for source
  snapshots, interchange, and explicitly versioned exports; do not introduce another primary
  storage engine without an explicit project decision. Strategies should consume normalized,
  time-bounded data rather than call a live provider during a backtest.
- Keep provider adapters separate from strategy and execution logic so the data source can be
  changed or cross-checked without rewriting strategies.
- Pin or record the AKQuant version when a result depends on engine behavior, especially order
  execution, fees, risk checks, or market-rule handling.
- Until local market snapshots are revalidated and explicitly approved, research demos should
  fetch remote AkShare data through the configured provider path. Do not use existing local
  Parquet or DuckDB snapshots as backtest input by default.
- The short-term research demo must use `AkShareMarketProvider` for its default remote path rather
  than calling an AKQuant data helper directly. Preserve the provider's source, storage, and
  retrieval timestamp in the generated report, including any explicit remote fallback.

## Cross-Platform Compatibility

- All committed code, scripts, and developer tools must work on both macOS and Windows. If a
  platform-specific behavior is unavoidable, document the reason, scope, and separate usage in
  `README.md`.
- Build filesystem paths with `pathlib.Path` or other standard cross-platform APIs. Do not hard-code
  `/`, `~`, `/tmp`, `/bin/sh`, `/bin/zsh`, Windows drive letters, or platform-specific path strings.
- Do not rely on case-sensitive filenames, executable permission bits, symlink behavior, or a
  particular current working directory.
- Invoke Python through `sys.executable` in application code and `python -m ...` in documentation;
  do not assume that `python3`, Bash, Zsh, `sed`, `grep`, or `make` is available on Windows.
- Use `subprocess` with an argument list and `shell=False` by default. Do not place shell pipelines,
  redirection, quoting tricks, or shell-only syntax in reusable tools.
- Use explicit text encodings and newline handling. Preserve source-specific encodings for imported
  A-share files instead of relying on the operating system default.
- Use `tempfile`, `os.environ`, `shutil`, and standard Python APIs for temporary files, environment
  variables, executable lookup, and file operations. Avoid Unix-only signals, `fork`, `/dev/null`,
  and shell startup-file assumptions.
- Provide equivalent macOS and Windows instructions for setup, activation, and common commands.
  When CI is added, run the relevant test and lint jobs on both operating systems.

## Repository Layout

- Place importable production code under `src/forbiddenland/`.
- Place tests under `tests/`, mirroring the source layout when practical.
- Keep downloaded source data under `data/raw/`, transformed local data under `data/processed/`,
  and generated reports under `reports/`.
- Do not commit ignored runtime data merely to make a local test pass. Use small, documented test
  fixtures when stable sample data is required.
- Add nested `AGENTS.md` files only when a subtree needs materially different rules.

## Large Data Artifacts

- Treat downloaded or generated Parquet, DuckDB, and other large analytical files as local or
  external artifacts. Never commit their payloads to Git.
- Keep raw source snapshots under `data/raw/`, normalized or derived datasets under
  `data/processed/`, and rebuildable local caches under `data/cache/`. Keep each dataset family
  together and include the source and as-of date in new filenames when those values are known.
- Keep only lightweight control-plane files in Git: acquisition/transformation code, schema notes,
  manifests, checksums, and reproducibility metadata. Store large payloads in approved object
  storage or release artifacts; Git LFS is not the default for this repository.
- Before handing off or changing a data artifact, inspect `git status --short` and verify
  `git check-ignore -v <path>`. An ignored local file being absent on another checkout is expected,
  not an implementation failure. If a small fixture is genuinely required, put it under
  `tests/fixtures/` and document why it is safe to track.
- The initial `stock_basic_data.parquet` snapshot was inspected on 2026-08-26 as an A-share
  security-master dataset with 5,892 rows and 16 columns. It belongs under
  `data/raw/stock_basic_data.parquet`; its source/provider and effective date are not encoded and
  must not be guessed.
- The local market snapshot `data/raw/stock_daily.parquet` is a large ignored artifact (currently
  about 14.6 million rows and 33 columns). It contains daily OHLCV, valuation fields, and
  `adj_factor`, but it is not a realtime or minute-level feed. Do not load the whole file into
  pandas when a DuckDB predicate can select the requested symbols and dates.
- Use `forbiddenland.integrations.akshare_compat` as the provider boundary when callers need
  AkShare-shaped results. `FORBIDDENLAND_MARKET_BACKEND` selects `local`, `remote`, or `hybrid`;
  remote is the default until local snapshots are revalidated and approved. Local reads require
  an explicit `FORBIDDENLAND_MARKET_BACKEND=local`, and hybrid may use the network only when
  `FORBIDDENLAND_ALLOW_REMOTE_FALLBACK=1` is explicit. Missing local data must not silently fall
  back to an older snapshot.
- Remote stock-history requests use a bounded retry policy for connection and timeout failures only.
  Configure it with `FORBIDDENLAND_REMOTE_RETRY_ATTEMPTS`,
  `FORBIDDENLAND_REMOTE_RETRY_BACKOFF_SECONDS`, and
  `FORBIDDENLAND_REMOTE_REQUEST_TIMEOUT_SECONDS`. When the primary remote stock-history endpoint
  remains unavailable, the provider may use AkShare's Tencent historical endpoint if
  `FORBIDDENLAND_REMOTE_ALTERNATE_SOURCE` is enabled (the default); the response provenance must
  identify that fallback. This is still remote acquisition and must never become a silent local or
  stale-cache substitution.
- The compatibility layer covers `stock_zh_a_hist`, the remote-only `stock_zh_a_hist_tx`,
  `stock_info_a_code_name`, and the four AKShare
  Tonghuashun concept endpoints (`stock_board_concept_name_ths`, `stock_board_concept_info_ths`,
  `stock_board_concept_index_ths`, and `stock_board_concept_summary_ths`) from the supplied
  snapshots. Tonghuashun local concept support is limited to the audited A-share `885/886` subset.
  Its `.TI` codes are a local index namespace and must not be treated as interchangeable with the
  remote page codes. Preserve unavailable local fields as missing: in particular quote amount,
  occasional concept volume, ranking, advancing/declining counts, capital flow, event narrative,
  and leading stock. The local summary is a catalog-date/current-member-count approximation, not
  historical event data.
- The HTTP market contract exposes searchable `stock`, `index`, and `concept` assets through
  `/api/v1/market/assets`; `/api/v1/market/bars` accepts the same `asset_type`. Local snapshots
  provide stock and audited Tonghuashun concept history. The curated broad-index catalog is
  available in every mode, but broad-index history remains remote-only until a local index
  snapshot is supplied and reviewed; local failures must stay explicit.
- The compatibility layer derives weekly/monthly bars from daily data. The supplied cumulative
  `adj_factor` is applied directly for hfq and normalized by the latest factor for qfq; any change
  to those formulas requires a contract test against a pinned provider sample. Do not represent
  the daily snapshot as `stock_zh_a_spot_em` realtime data.

## Code Design

- Keep data acquisition, normalization, analysis, and presentation responsibilities separable.
- The frontend is an independent project under `frontend/`; it must communicate with the Python
  backend through versioned API contracts and must never open DuckDB/Parquet or call AkShare or
  AKQuant directly. Keep API routes thin; market-data access, calculations, provenance, and
  backtest orchestration belong to backend application/infrastructure layers.
- The local FastAPI service listens on `127.0.0.1:9092` by default, and the Vite development
  proxy must target that port. Keep `FORBIDDENLAND_API_PORT` overrides and frontend proxy changes
  documented together.
- The frontend's initial market query range is calculated at runtime from the local calendar date:
  one calendar month before today through today. Do not replace this with a frozen date literal.
- Keep calculations deterministic where possible: pass data and parameters explicitly instead of
  reading global state inside analysis functions.
- Preserve raw source values until normalization; do not silently correct, fill, or discard invalid
  data.
- Represent A-share security codes as strings so leading zeroes are preserved.
- Distinguish missing data from numeric zero throughout parsing, calculation, and reporting.
- Record or propagate enough context to identify the data source, observation date, adjustment mode,
  and relevant calculation parameters.
- Prefer small modules and direct code over speculative abstraction. Introduce shared abstractions
  only after they remove concrete duplication or enforce a real domain boundary.

## Frontend Watchlists

- Keep personal watchlist groups browser-local under `forbiddenland.watchlist.v1`. Do not commit
  their contents or silently move them into analytical DuckDB files. A future multi-device or
  multi-user persistence change requires an explicit backend contract and migration decision.
- Use the official `lightweight-charts` package directly for market charts. Keep the TradingView
  attribution enabled, create chart instances only for visible items, observe container resizing,
  and remove each instance when its React component unmounts. The current market contract is
  daily-only, so both compact cards and detail views render daily candlesticks; do not label or
  transform date-only bars as minute data until an interval-aware backend contract exists.
- The watchlist grid supports page sizes 4, 6, and 9. Preserve independent loading and error state
  per asset so one unavailable provider response does not hide otherwise valid charts.

## Formatting And Style

- Use UTF-8 source files, LF line endings, four-space indentation, and no tabs.
- Follow the Ruff configuration in `pyproject.toml`; the current line-length limit is 100.
- Use type annotations for public functions and for internal interfaces where they clarify data
  shape or optional values.
- Use descriptive English identifiers. Chinese text is appropriate for user-facing reports and
  A-share domain labels when it improves clarity.
- Keep comments focused on non-obvious domain decisions, data caveats, or algorithmic reasoning.
- Do not reformat or refactor unrelated files as part of a scoped change.

## Errors And Data Quality

- Do not silently swallow network, parsing, or data-quality failures.
- When partial results are valid, retain them and report the affected source, symbol, field, and
  reason for the missing result.
- Retry only transient remote connection failures with a finite, observable policy; do not retry
  invalid parameters, malformed responses, or data-quality errors.
- If a remote alternate endpoint supplies a result, preserve that endpoint in the result
  provenance so analyses can be compared and reproduced.
- Do not silently substitute stale cached values after a live-data failure.
- Avoid broad exception handling unless it adds context and preserves the original exception or
  deliberately isolates one failed item in a batch.
- Never log or commit credentials, access tokens, cookies, or private account data.

## Tests And Verification

- Add or update tests when behavior changes.
- Cover normal values, missing values, boundary cases, and malformed source data for calculation and
  parsing code.
- Mock external services in unit tests; tests must not depend on a live market-data endpoint.
- When a unit test needs concrete A-share securities, use 寒武纪 (`688256`), 拓荆科技 (`688072`),
  and 生益科技 (`600183`) as the standard fixtures. Keep their codes as strings and use
  deterministic local or in-memory data instead of fetching live quotes.
- Before completing a change, run the checks relevant to the files changed. The default full checks
  are:

  ```bash
  python -m pytest
  ruff format --check .
  ruff check .
  python -m compileall -q src tests scripts research
  git diff --check
  ```

- If a check cannot run because a tool or dependency is unavailable, state that explicitly instead
  of reporting it as passed.

## Git Discipline

- Inspect `git status` and the complete diff before committing.
- Preserve user changes and unrelated untracked files.
- Keep commits narrowly scoped and use an imperative, descriptive commit message.
- When a feature changes a durable project behavior, data contract, workflow, or engineering
  constraint, update the applicable `AGENTS.md` in the same change. Record stable rules and
  boundaries, not transient implementation details; review the rule diff together with the feature
  diff before committing.
- Do not commit `.env` files, credentials, downloaded market data, caches, databases, or generated
  reports unless the task explicitly requires a reviewed fixture or artifact.
- Do not rewrite published history or use destructive Git commands unless explicitly requested.

## Future Rules

Add confirmed conventions here or in a focused document as the project develops. Likely areas
include data-provider contracts, package boundaries, financial indicator definitions, adjustment and
calendar rules, report schemas, backtest assumptions, and release or CI requirements.
