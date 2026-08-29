#!/bin/sh

set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
PYTHON="$PROJECT_ROOT/.venv/bin/python"
VITE_BIN="$PROJECT_ROOT/frontend/node_modules/.bin/vite"

if [ ! -x "$PYTHON" ]; then
    printf 'Missing Python environment: %s\n' "$PYTHON" >&2
    printf 'Run `python scripts/bootstrap.py` from the repository root first.\n' >&2
    exit 1
fi

if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
    printf 'Node.js and npm are required to start the frontend.\n' >&2
    printf 'Use Node.js 22.14.x and run `python scripts/bootstrap.py` first.\n' >&2
    exit 1
fi

if [ ! -e "$VITE_BIN" ]; then
    printf 'Frontend dependencies are missing: %s\n' "$VITE_BIN" >&2
    printf 'Run `python scripts/bootstrap.py` from the repository root first.\n' >&2
    exit 1
fi

cd "$PROJECT_ROOT"
exec "$PYTHON" "$PROJECT_ROOT/scripts/dev.py" "$@"
