from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
START_SCRIPT = PROJECT_ROOT / "scripts" / "start.sh"


def test_start_script_is_an_ubuntu_bash_entrypoint() -> None:
    content = START_SCRIPT.read_text(encoding="utf-8")

    assert content.startswith("#!/usr/bin/env bash\n")
    assert 'exec "$PYTHON" "$PROJECT_ROOT/scripts/dev.py" "$@"' in content
    assert "node_modules/.bin/vite" in content
