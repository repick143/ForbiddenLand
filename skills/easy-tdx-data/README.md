# easy-tdx-data skill

A reusable research skill for the `easy-tdx` TDX/MAC data source. It prefers the project-local
`.venv/bin/easy-tdx` binary, keeps live/offline paths explicit, and formats market-data results with
source, timestamp, frequency, adjustment, unit, and quality metadata.

## Install

From the repository root:

```bash
python scripts/bootstrap.py --profile data
.venv/bin/python -m pip install "easy-tdx==1.28.1"
.venv/bin/python -m pip check
```

To make the skill available to Codex, run the repository installer from the project root. It links
the checked-out directory into the user skill home (`$CODEX_HOME` defaults to `~/.codex`) and
refuses to overwrite an existing destination:

```bash
python scripts/install_local_skills.py
```

The link keeps the repository source canonical: edits to `SKILL.md` and `references/` are visible
locally immediately, so a copy/reinstall is not needed after each skill update. Re-run the command
after moving the repository or repairing a link. If the host loads user skills from
`~/.agents/skills` instead, pass that directory explicitly:

```bash
python scripts/install_local_skills.py --skill-home "$HOME/.agents/skills"
```

Restart Codex (or open a new session) after creating the link so the skill enters the session's
skill inventory.

The distribution is `easy-tdx`; the import name is `easy_tdx`. Install optional extras only for the
capability you need:

```bash
.venv/bin/python -m pip install "easy-tdx[science]==1.28.1"   # Spearman IC
.venv/bin/python -m pip install "easy-tdx[warehouse]==1.28.1" # DuckDB warehouse
.venv/bin/python -m pip install "easy-tdx[web]==1.28.1"      # FastAPI/Uvicorn/UI
```

The package requires `pandas>=2,<3`. A fresh project environment may therefore need pandas 2.x;
run the repository test suite after installing it. This skill does not install into a global Python
interpreter and does not store credentials.

## Usage

The skill is intended for requests such as:

- daily, weekly, or minute K-lines and technical indicators;
- quotes, five-level books, intraday/tick data, auctions, boards, and capital flow;
- Chanlun, VSA input preparation, TongdaXin formulas, factors, screening, and backtests;
- repeated DuckDB/offline queries and reproducible data exports.

The binary returns JSON by default. Use `--table` only for human display; keep the JSON command in
the final report for reproduction:

```bash
.venv/bin/easy-tdx kline SH 600183 --period 5MIN --count 200 --adjust NONE
.venv/bin/easy-tdx indicator MACD,KDJ,RSI -m SH -c 600183 --count 30
```

## Scope

This is a research/monitoring wrapper, not a broker or order API. TDX “realtime” is polling, not
exchange push. The skill does not silently replace ForbiddenLand's AkShare provider, DuckDB
contracts, or AKQuant backtest engine.

## References

- `SKILL.md` — runtime detection, routing, defaults, validation, and response template
- `references/market-data.md` — command/API map, periods, adjustments, units, provenance
- `references/research.md` — indicators, factors, formulas, Chanlun, and VSA workflow
- `references/backtest-storage-web.md` — backtesting, screening, warehouse, and Web capabilities
- `references/troubleshooting.md` — installation, connectivity, optional extras, and known mismatches
