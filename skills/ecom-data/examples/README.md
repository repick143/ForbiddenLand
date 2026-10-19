# ecom-data Examples

Run the demo (self-contained, resolves the repo and `.venv` itself):

```bash
python skills/ecom-data/examples/usage.py
```

The example shows both call styles:

1. Directly executable CLI: `skills/ecom-data/scripts/ecom-data bars ...`
   (no `python -m` prefix; requires the project `.venv`).
2. Python API in your own code: `sys.path` insert of
   `skills/ecom-data/scripts` + `from ecom_data import EcomData, BarRequest`.
