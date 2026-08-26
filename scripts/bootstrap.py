"""Initialize the local ForbiddenLand development environment on macOS or Windows."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VENV = PROJECT_ROOT / ".venv"
REQUIRED_PYTHON = (3, 12)
DATA_DIRECTORIES = (
    PROJECT_ROOT / "data" / "raw",
    PROJECT_ROOT / "data" / "processed",
    PROJECT_ROOT / "data" / "cache",
    PROJECT_ROOT / "reports",
)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a Python 3.12 environment and install ForbiddenLand dependencies."
    )
    parser.add_argument(
        "--profile",
        choices=("core", "data", "dev", "full"),
        default="full",
        help="Dependency profile to install (default: full).",
    )
    parser.add_argument(
        "--venv",
        type=Path,
        default=DEFAULT_VENV,
        help="Virtual-environment directory (default: .venv).",
    )
    parser.add_argument(
        "--skip-pip-upgrade",
        action="store_true",
        help="Do not upgrade pip, setuptools, and wheel before installation.",
    )
    parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="Skip import, compile, test, and lint checks after installation.",
    )
    return parser.parse_args(argv)


def project_python_version() -> str:
    version_file = PROJECT_ROOT / ".python-version"
    try:
        return version_file.read_text(encoding="ascii").strip()
    except OSError:
        return "3.12.10"


def require_python() -> None:
    current = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info[:2] != REQUIRED_PYTHON:
        expected = project_python_version()
        raise RuntimeError(
            f"Python {current} is active; this project requires Python 3.12.x "
            f"(recommended: {expected}). Run this script with the selected Python interpreter."
        )

    expected = project_python_version()
    if expected and current != expected:
        print(
            f"Warning: .python-version requests {expected}, "
            f"but the active interpreter is {current}."
        )


def venv_python(venv_dir: Path) -> Path:
    relative = Path("Scripts") / "python.exe" if os.name == "nt" else Path("bin") / "python"
    return venv_dir / relative


def command_label(command: Sequence[Path | str]) -> str:
    return " ".join(repr(str(part)) for part in command)


def run(command: Sequence[Path | str]) -> None:
    print(f"+ {command_label(command)}")
    subprocess.run([str(part) for part in command], cwd=PROJECT_ROOT, check=True, shell=False)


def interpreter_version(executable: Path) -> str:
    result = subprocess.run(
        [str(executable), "-c", "import sys; print('.'.join(map(str, sys.version_info[:3])))"],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        shell=False,
    )
    return result.stdout.strip()


def create_or_validate_venv(venv_dir: Path) -> Path:
    venv_dir = venv_dir.expanduser()
    python = venv_python(venv_dir)

    if venv_dir.exists():
        if not python.is_file():
            raise RuntimeError(
                f"Existing virtual environment at {venv_dir} is incomplete; "
                "choose another --venv or repair it manually."
            )
        version = interpreter_version(python)
        if not version.startswith("3.12."):
            raise RuntimeError(
                f"Existing virtual environment uses Python {version}; "
                "it will not be deleted automatically. Choose another --venv "
                "or recreate it manually."
            )
        print(f"Reusing Python {version} environment: {venv_dir}")
        return python

    print(f"Creating virtual environment: {venv_dir}")
    venv.EnvBuilder(with_pip=True, clear=False, symlinks=False).create(venv_dir)
    if not python.is_file():
        raise RuntimeError(f"Virtual environment creation did not produce {python}")
    return python


def install_target(profile: str) -> str:
    extras = {
        "core": "",
        "data": "[data]",
        "dev": "[dev]",
        "full": "[dev,data]",
    }[profile]
    return f".{extras}"


def install_dependencies(python: Path, profile: str, skip_pip_upgrade: bool) -> None:
    if not skip_pip_upgrade:
        run([python, "-m", "pip", "install", "--upgrade", "pip", "setuptools", "wheel"])
    run([python, "-m", "pip", "install", "--editable", install_target(profile)])


def create_data_directories() -> None:
    for directory in DATA_DIRECTORIES:
        directory.mkdir(parents=True, exist_ok=True)
        print(f"Ready: {directory.relative_to(PROJECT_ROOT)}")


def verify_imports(python: Path, profile: str) -> None:
    modules = ["forbiddenland", "akquant"]
    if profile in {"data", "full"}:
        modules.extend(["akshare", "duckdb", "pyarrow"])
    module_literal = repr(modules)
    code = (
        "import importlib, importlib.metadata\n"
        f"modules = {module_literal}\n"
        "for name in modules:\n"
        "    importlib.import_module(name)\n"
        "    try:\n"
        "        version = importlib.metadata.version(name)\n"
        "    except importlib.metadata.PackageNotFoundError:\n"
        "        version = 'local'\n"
        "    print(f'{name}: {version}')\n"
    )
    run([python, "-c", code])


def run_checks(python: Path, profile: str) -> None:
    run([python, "-m", "compileall", "-q", "src", "tests", "scripts", "research"])
    if profile in {"dev", "full"}:
        run([python, "-m", "pytest"])
        run([python, "-m", "ruff", "format", "--check", "."])
        run([python, "-m", "ruff", "check", "."])
    git = shutil.which("git")
    if git:
        run([git, "diff", "--check"])


def print_next_steps(venv_dir: Path) -> None:
    try:
        relative = venv_dir.relative_to(PROJECT_ROOT)
    except ValueError:
        relative = venv_dir
    print("\nInitialization complete.")
    if os.name == "nt":
        print(f"PowerShell: {relative / 'Scripts' / 'Activate.ps1'}")
        print(f"Command Prompt: {relative / 'Scripts' / 'activate.bat'}")
    else:
        print(f"macOS/Linux: source {relative / 'bin' / 'activate'}")
    print(f"Interpreter: {venv_python(venv_dir)}")


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        require_python()
        venv_dir = args.venv.expanduser()
        if not venv_dir.is_absolute():
            venv_dir = PROJECT_ROOT / venv_dir
        python = create_or_validate_venv(venv_dir)
        create_data_directories()
        install_dependencies(python, args.profile, args.skip_pip_upgrade)
        if not args.skip_checks:
            verify_imports(python, args.profile)
            run_checks(python, args.profile)
        print_next_steps(venv_dir)
        return 0
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"Initialization failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
