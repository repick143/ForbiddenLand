# line-fetcher Examples

Run the demo (self-contained, resolves the repo and `.venv` itself):

```bash
python skills/line-fetcher/examples/usage.py
```

The example shows both call styles:

1. Skill launcher CLI: `skills/line-fetcher/scripts/line-fetcher bars ...` (or the
   installed `line-fetcher` console command; no `python -m` prefix).
2. Python API in your own code: `from line_fetcher import LineFetcher, BarRequest`
   (library installed from `src/line_fetcher` via `pip install -e ".[dev,data]"`).
