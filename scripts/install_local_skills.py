#!/usr/bin/env python3
"""Link repository-local Codex skills into a user skill directory."""

from __future__ import annotations

import argparse
import os
from collections.abc import Sequence
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SKILLS = ("easy-tdx-data",)


def validate_skill_name(name: str) -> str:
    """Accept only a single directory name for a repository-local skill."""

    if not name or name in {".", ".."} or Path(name).name != name:
        raise ValueError(f"Invalid local skill name: {name!r}")
    return name


def default_skill_home() -> Path:
    """Return the Codex skill directory for the current user."""

    codex_home = os.environ.get("CODEX_HOME")
    base = Path(codex_home).expanduser() if codex_home else Path.home() / ".codex"
    return base / "skills"


def skill_source(name: str, project_root: Path = PROJECT_ROOT) -> Path:
    """Resolve and validate a skill source within the repository."""

    name = validate_skill_name(name)
    source = (project_root / "skills" / name).resolve()
    skills_root = (project_root / "skills").resolve()
    if source.parent != skills_root:
        raise RuntimeError(f"Local skill source must be directly under {skills_root}: {source}")
    if not source.is_dir() or not (source / "SKILL.md").is_file():
        raise RuntimeError(f"Local skill source is missing or invalid: {source}")
    return source


def ensure_skill_link(source: Path, destination: Path) -> str:
    """Create a safe directory link, or verify an existing link, without overwriting."""

    source = source.resolve()
    destination = destination.expanduser()
    if not source.is_dir() or not (source / "SKILL.md").is_file():
        raise RuntimeError(f"Local skill source is missing or invalid: {source}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        current = destination.resolve(strict=False)
        if current == source:
            return "already linked"
        raise RuntimeError(
            f"Refusing to replace existing skill link {destination} -> {current}; expected {source}"
        )
    if destination.exists():
        raise RuntimeError(
            f"Refusing to overwrite existing skill path {destination}; "
            "move it aside or remove it explicitly first"
        )

    destination.symlink_to(source, target_is_directory=True)
    return "linked"


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Link repository-local skills into the Codex user skill directory."
    )
    parser.add_argument(
        "--skill",
        dest="skills",
        action="append",
        metavar="NAME",
        help="Skill directory under skills/ (repeatable; defaults to easy-tdx-data).",
    )
    parser.add_argument(
        "--skill-home",
        type=Path,
        help="Destination skill directory (default: ${CODEX_HOME:-$HOME/.codex}/skills).",
    )
    return parser.parse_args(argv)


def install_skills(
    skill_names: Sequence[str],
    skill_home: Path,
    project_root: Path = PROJECT_ROOT,
) -> list[tuple[str, Path, str]]:
    """Install each named local skill and return its status."""

    skill_home = skill_home.expanduser().resolve()
    sources = [(name, skill_source(name, project_root)) for name in skill_names]
    results: list[tuple[str, Path, str]] = []
    for name, source in sources:
        destination = skill_home / name
        status = ensure_skill_link(source, destination)
        results.append((name, destination, status))
    return results


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    skill_names = tuple(args.skills or DEFAULT_SKILLS)
    skill_home = args.skill_home or default_skill_home()
    try:
        results = install_skills(skill_names, skill_home)
    except (OSError, RuntimeError) as exc:
        print(f"Local skill installation failed: {exc}")
        return 1

    for name, destination, status in results:
        print(f"{name}: {status} at {destination}")
    print("Source directories remain canonical; later edits are available through the link.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
