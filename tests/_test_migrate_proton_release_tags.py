"""Name GE-Proton releases in Wine configurations by their release tags."""

from pathlib import Path

import pytest

from qtris import settings
from qtris.migrations import migrate_proton_release_tags
from qtris.util.wine import proton
from qtris.util.yaml import read_yaml_from_file, write_yaml_to_file


def test_ge_proton_folder_names_become_release_tags(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "runners").mkdir()
    (tmp_path / "games").mkdir()
    versions = {
        "runners/wine.yml": "GE-Proton11-12-x86_64",
        "games/tagged.yml": "GE-Proton10-25",
        "games/plain-wine.yml": "lutris-7.2-2-x86_64",
        "games/valve.yml": "Proton 11.0",
    }
    for name, version in versions.items():
        write_yaml_to_file({"wine": {"version": version}}, str(tmp_path / name))
    write_yaml_to_file({"game": {"exe": "game.exe"}}, str(tmp_path / "games" / "no-version.yml"))
    games = [{"configpath": Path(name).stem} for name in versions if name.startswith("games/")]
    monkeypatch.setattr(settings, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(proton.LINUX_SYSTEM, "arch", "x86_64")
    monkeypatch.setattr(
        migrate_proton_release_tags, "get_games_by_runner", lambda runner: [*games, {"configpath": "no-version"}]
    )

    migrate_proton_release_tags.migrate()

    def version(name: str) -> str:
        return read_yaml_from_file(str(tmp_path / name))["wine"]["version"]

    assert version("runners/wine.yml") == "GE-Proton11-12"
    assert version("games/tagged.yml") == "GE-Proton10-25"
    assert version("games/plain-wine.yml") == "lutris-7.2-2-x86_64"
    assert version("games/valve.yml") == "Proton 11.0"
    assert read_yaml_from_file(str(tmp_path / "games" / "no-version.yml")) == {"game": {"exe": "game.exe"}}
