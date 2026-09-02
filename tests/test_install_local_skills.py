from pathlib import Path

import pytest

from scripts import install_local_skills


def make_skill(root: Path, name: str = "demo") -> Path:
    source = root / "skills" / name
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text("---\nname: demo\n---\n", encoding="utf-8")
    return source


def test_default_skill_home_honors_codex_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path / "codex"))

    assert install_local_skills.default_skill_home() == tmp_path / "codex" / "skills"


@pytest.mark.parametrize("name", ["", ".", "..", "nested/demo", "/tmp/demo"])
def test_skill_name_rejects_paths(name: str) -> None:
    with pytest.raises(ValueError, match="Invalid local skill name"):
        install_local_skills.validate_skill_name(name)


def test_ensure_skill_link_is_idempotent(tmp_path: Path) -> None:
    source = make_skill(tmp_path)
    destination = tmp_path / "home" / "skills" / "demo"

    assert install_local_skills.ensure_skill_link(source, destination) == "linked"
    assert destination.is_symlink()
    assert destination.resolve() == source.resolve()
    (source / "references").mkdir()
    (source / "references" / "note.md").write_text("updated", encoding="utf-8")
    assert (destination / "references" / "note.md").read_text(encoding="utf-8") == "updated"
    assert install_local_skills.ensure_skill_link(source, destination) == "already linked"


def test_ensure_skill_link_refuses_existing_path(tmp_path: Path) -> None:
    source = make_skill(tmp_path)
    destination = tmp_path / "home" / "skills" / "demo"
    destination.mkdir(parents=True)

    with pytest.raises(RuntimeError, match="Refusing to overwrite"):
        install_local_skills.ensure_skill_link(source, destination)


def test_install_skills_resolves_repository_source(tmp_path: Path) -> None:
    source = make_skill(tmp_path)
    destination_home = tmp_path / "home" / "skills"

    results = install_local_skills.install_skills(("demo",), destination_home, tmp_path)

    assert results == [("demo", destination_home / "demo", "linked")]
    assert (destination_home / "demo").resolve() == source.resolve()
