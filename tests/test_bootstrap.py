import os
from pathlib import Path

from scripts.bootstrap import install_target, project_python_version, venv_python


def test_project_python_version_is_pinned() -> None:
    assert project_python_version() == "3.12.10"


def test_install_target_profiles() -> None:
    assert install_target("core") == "."
    assert install_target("data") == ".[data]"
    assert install_target("dev") == ".[dev]"
    assert install_target("full") == ".[dev,data]"


def test_venv_python_uses_platform_layout() -> None:
    expected = Path(".venv") / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    assert venv_python(Path(".venv")) == expected
