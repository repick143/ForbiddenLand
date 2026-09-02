import os
from pathlib import Path

import pytest

from scripts import bootstrap
from scripts.bootstrap import (
    frontend_install_requested,
    install_target,
    parse_args,
    parse_node_version,
    project_python_version,
    venv_python,
)


def test_project_python_version_is_pinned() -> None:
    assert project_python_version() == "3.12.10"


def test_install_target_profiles() -> None:
    assert install_target("core") == "."
    assert install_target("data") == ".[data]"
    assert install_target("web") == ".[data,web]"
    assert install_target("dev") == ".[dev]"
    assert install_target("full") == ".[dev,data,web]"


def test_data_profiles_verify_easy_tdx_import() -> None:
    commands: list[list[str]] = []

    def fake_run(command, *, cwd=bootstrap.PROJECT_ROOT):
        del cwd
        commands.append([str(part) for part in command])

    original = bootstrap.run
    try:
        bootstrap.run = fake_run
        bootstrap.verify_imports(Path(".venv/bin/python"), "data")
    finally:
        bootstrap.run = original

    assert "easy_tdx" in commands[0][-1]


def test_venv_python_uses_platform_layout() -> None:
    expected = Path(".venv") / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert venv_python(Path(".venv")) == expected


def test_full_profile_initializes_frontend_by_default() -> None:
    args = parse_args([])

    assert args.profile == "full"
    assert frontend_install_requested(args.profile, args.skip_frontend)


def test_frontend_initialization_can_be_skipped() -> None:
    args = parse_args(["--skip-frontend"])

    assert args.skip_frontend is True
    assert not frontend_install_requested(args.profile, args.skip_frontend)


def test_frontend_install_uses_lockfile(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[tuple[list[str], Path]] = []

    monkeypatch.setattr(bootstrap, "npm_executable", lambda: "/usr/bin/npm")
    monkeypatch.setattr(
        bootstrap,
        "run",
        lambda command, *, cwd: commands.append(([str(part) for part in command], cwd)),
    )

    bootstrap.install_frontend_dependencies()

    assert commands == [(["/usr/bin/npm", "ci"], bootstrap.FRONTEND_ROOT)]


@pytest.mark.parametrize(
    ("value", "expected"),
    [("v22.14.0", (22, 14, 0)), ("22.15.1", (22, 15, 1))],
)
def test_parse_node_version(value: str, expected: tuple[int, int, int]) -> None:
    assert parse_node_version(value) == expected


def test_parse_node_version_rejects_malformed_value() -> None:
    with pytest.raises(RuntimeError, match="Unable to parse Node.js version"):
        parse_node_version("node-version")
