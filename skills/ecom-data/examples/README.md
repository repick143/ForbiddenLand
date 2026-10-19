# ecom-data Examples

Run the demo (self-contained, resolves the repo and `.venv` itself):

```bash
python skills/ecom-data/examples/usage.py
```

The example shows both call styles:

1. Skill launcher CLI: `skills/ecom-data/scripts/ecom-data bars ...` (or the
   installed `ecom-data` console command; no `python -m` prefix).
2. Python API in your own code: `from ecom_data import EcomData, BarRequest`
   (library installed from `src/ecom_data` via `pip install -e ".[dev,data]"`).
